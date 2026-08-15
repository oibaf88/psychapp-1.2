"""
Agent 4 — psychosocial context extraction, and the deterministic index
built from it.

Why this exists
---------------
Emotional deterioration is usually the *last* thing to change. What changes
first is the person's situation: they move in with someone "for a while",
they stop seeing the gym crowd, a benefit is withdrawn, a grandmother dies,
they go back to a flat where people use. Each of those sentences reads as
small talk, and the previous pipeline threw all of them away: Agent 2 scores
rumination and ideation, and the structural score only reads four daily
numbers. Nothing in the system could see a person's life narrowing.

Two clearly separated halves
----------------------------
1. **Extraction (this file, top half).** An LLM reads the patient's text and
   returns structured social determinants with a literal supporting quote.
   Like every model output in this codebase it is an *inference*, validated
   against a strict schema and stored as such; invalid output is discarded
   whole.
2. **Scoring (this file, bottom half).** A deterministic, inspectable
   function turns those stored observations into a vulnerability index and
   an acute-destabilisation flag. **No model is in this path.** The risk
   engine consumes only these numbers, so a hallucinated observation can
   move an index the clinician can see and audit, but it can never "decide"
   a level on its own — every new rule requires convergence with an
   independent signal.

Clinical grounding of the weights
---------------------------------
The domain weights below rank the determinants that the suicide-prevention
and addiction-relapse literature consistently associates with proximal risk:
housing instability, loss of social support and connectedness, interpersonal
loss events, economic shock, disengagement from care, and exposure to using
environments. They are deliberately coarse and are exposed to the therapist
in the panel and the manual, because a weight nobody can see is a weight
nobody can challenge. They are NOT a validated instrument and must not be
read as one.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.content.prompts import (
    AGENT4_DOMAIN_CATEGORIES,
    AGENT4_SYSTEM_PROMPT,
    AGENT4_TOOL_SCHEMA,
)
from app.models import PsychosocialObservation
from app.services import agent2_trace
from app.services.llm import StructuredAnalysisError, get_llm_provider

logger = logging.getLogger("psychapp.psychosocial")

AGENT_ROLE = "agent4_psychosocial"
MAX_QUOTE_CHARS = 300
MAX_SUMMARY_CHARS = 400

# How long an observation keeps counting toward the live index. Social
# circumstances persist far longer than a linguistic marker, so this window is
# generous; a therapist can always refute a stale one.
ACTIVE_WINDOW_DAYS = 90
# A "change" is only acute for a fortnight. This is the window in which the
# apparently-innocuous signals matter most.
ACUTE_CHANGE_WINDOW_DAYS = 14

# Relative weight of each domain in the vulnerability index (0-1).
DOMAIN_WEIGHTS: dict[str, float] = {
    "housing": 1.00,
    "social_support": 1.00,
    "loss_event": 0.90,
    "connectedness": 0.85,
    "substance_environment": 0.85,
    "healthcare_access": 0.80,
    "economic": 0.80,
    "family": 0.75,
    "cohabitation": 0.70,
    "occupation": 0.60,
    "means_access": 0.95,
    "stigma": 0.55,
    "legal": 0.50,
}

# Categories that constitute an acute adverse change when freshly reported.
# These are the "small" sentences that precede crises.
ACUTE_CHANGE_CATEGORIES = {
    "housing_homeless",
    "housing_eviction_risk",
    "housing_temporary",
    "housing_precarious",
    "lives_with_people_who_use",
    "cohabitation_conflict",
    "support_absent",
    "isolation_increasing",
    "family_conflict",
    "family_estranged",
    "benefit_loss",
    "food_insecurity",
    "debt",
    "job_loss",
    "bereavement",
    "breakup",
    "relationship_loss",
    "pet_loss",
    "other_loss",
    "loss_of_routine",
    "treatment_dropout",
    "medication_access_problem",
    "using_environment_exposure",
    "means_access_reported",
}

DOMAIN_LABELS = {
    "housing": "Vivienda",
    "cohabitation": "Convivencia",
    "social_support": "Apoyo social",
    "family": "Familia",
    "economic": "Situación económica",
    "occupation": "Ocupación",
    "legal": "Situación legal",
    "healthcare_access": "Acceso a tratamiento",
    "stigma": "Estigma",
    "loss_event": "Pérdidas y rupturas",
    "connectedness": "Vínculos y rutina",
    "means_access": "Acceso a medios lesivos",
    "substance_environment": "Entorno de consumo",
}

CATEGORY_LABELS = {
    "housing_stable": "Vivienda estable",
    "housing_precarious": "Vivienda precaria",
    "housing_temporary": "Alojamiento temporal",
    "housing_homeless": "Sin hogar",
    "housing_eviction_risk": "Riesgo de perder la vivienda",
    "housing_institution": "Recurso residencial / institución",
    "lives_alone": "Vive solo/a",
    "lives_with_family": "Vive con familia",
    "lives_with_partner": "Vive en pareja",
    "lives_shared": "Vivienda compartida",
    "lives_with_people_who_use": "Convive con personas que consumen",
    "cohabitation_conflict": "Conflicto de convivencia",
    "support_strong": "Apoyo social sólido",
    "support_limited": "Apoyo social limitado",
    "support_absent": "Sin apoyo social",
    "isolation_increasing": "Aislamiento creciente",
    "new_supportive_relationship": "Nuevo vínculo de apoyo",
    "family_supportive": "Familia que apoya",
    "family_conflict": "Conflicto familiar",
    "family_estranged": "Ruptura familiar",
    "family_caregiving_burden": "Sobrecarga de cuidados",
    "family_unaware": "Familia no informada",
    "income_stable": "Ingresos estables",
    "income_precarious": "Ingresos precarios",
    "debt": "Deudas",
    "food_insecurity": "Inseguridad alimentaria",
    "benefit_loss": "Pérdida de ayuda o prestación",
    "financial_dependence": "Dependencia económica",
    "employed": "Con empleo",
    "unemployed": "Sin empleo",
    "job_loss": "Pérdida de empleo",
    "studying": "Estudiando",
    "sick_leave": "Baja laboral",
    "work_stress": "Estrés laboral",
    "legal_proceedings": "Procedimiento legal abierto",
    "legal_none": "Sin asuntos legales",
    "treatment_engaged": "Vinculado al tratamiento",
    "treatment_dropout": "Abandono de tratamiento",
    "medication_access_problem": "Problema de acceso a medicación",
    "appointment_barrier": "Barrera para acudir a citas",
    "stigma_experienced": "Estigma vivido",
    "disclosure_fear": "Miedo a revelar su situación",
    "bereavement": "Duelo",
    "breakup": "Ruptura de pareja",
    "relationship_loss": "Pérdida de una relación",
    "pet_loss": "Pérdida de un animal de compañía",
    "other_loss": "Otra pérdida",
    "meaningful_activity": "Actividad con sentido",
    "community_belonging": "Pertenencia a un grupo",
    "future_plans": "Planes de futuro",
    "loss_of_routine": "Pérdida de rutina",
    "means_access_reported": "Acceso referido a medios lesivos",
    "means_restricted": "Medios restringidos",
    "using_environment_exposure": "Exposición a entorno de consumo",
    "environment_protective": "Entorno protector",
}

# Reverse lookup so a category can be validated against the domain it claims.
CATEGORY_DOMAIN = {
    category: domain
    for domain, categories in AGENT4_DOMAIN_CATEGORIES.items()
    for category in categories
}


# ----------------------------------------------------------- extraction ----
class PsychosocialObservationIn(BaseModel):
    """Strict boundary between untrusted model output and the database."""

    model_config = ConfigDict(extra="forbid", strict=True)

    domain: str
    category: str
    valence: Literal["risk", "protective", "neutral"]
    intensity: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    is_change: bool
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_CHARS)
    quote: str = Field(max_length=2000)


class PsychosocialExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    has_psychosocial_content: bool
    observations: list[PsychosocialObservationIn] = Field(max_length=8)


@dataclass(frozen=True)
class ExtractionOutcome:
    trace_id: uuid.UUID | None
    status: str
    observation_ids: list[uuid.UUID] = field(default_factory=list)


def _coherent(observation: PsychosocialObservationIn) -> bool:
    """Drop observations whose category does not belong to their domain.

    The JSON schema constrains both fields independently, so the model can
    still emit a valid-but-incoherent pair. Rather than guess which half was
    meant, the row is discarded.
    """
    return CATEGORY_DOMAIN.get(observation.category) == observation.domain


def _quote_is_grounded(quote: str, source_text: str) -> bool:
    """Require the quote to actually appear in the patient's text.

    This is the cheapest available defence against a fabricated citation.
    A therapist reading `evidence_quote` must be able to trust that the
    patient really wrote those words, so an ungrounded quote is dropped
    rather than shown.
    """
    normalised_quote = " ".join(quote.split()).casefold()
    if len(normalised_quote) < 4:
        return False
    return normalised_quote in " ".join(source_text.split()).casefold()


def extract_and_store(
    db: Session,
    user_id,
    text: str,
    *,
    source_type: str,
    source_id: uuid.UUID,
    correlation_id: uuid.UUID,
    observed_at: datetime | None = None,
) -> ExtractionOutcome:
    """Run Agent 4 over one text and persist what it found.

    Never raises into the patient-facing flow. Every failure mode — the
    trace not committing, the provider erroring, invalid output — leaves the
    conversation and the deterministic risk engine untouched.
    """
    try:
        trace = agent2_trace.start(
            db,
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
            correlation_id=correlation_id,
            agent_role=AGENT_ROLE,
        )
    except agent2_trace.TracePersistenceError:
        logger.error("Agent 4 skipped because its trace could not be persisted")
        return ExtractionOutcome(None, "trace_persistence_error")

    try:
        provider_result = get_llm_provider().analyze_structured(
            AGENT4_SYSTEM_PROMPT,
            text,
            AGENT4_TOOL_SCHEMA,
        )
        extraction = PsychosocialExtraction.model_validate(provider_result.value)
    except Exception as exc:  # noqa: BLE001
        agent2_trace.mark_failed(db, trace, exc)
        logger.error("Agent 4 extraction failed safely: %s", type(exc).__name__)
        return ExtractionOutcome(trace.id, trace.status)

    when = observed_at or datetime.utcnow()
    rows: list[PsychosocialObservation] = []
    for item in extraction.observations:
        if not _coherent(item):
            logger.warning("Agent 4 returned %s outside domain %s; dropped", item.category, item.domain)
            continue
        quote = " ".join(item.quote.split())[:MAX_QUOTE_CHARS]
        if not _quote_is_grounded(quote, text):
            logger.warning("Agent 4 quote not found in the source text; observation dropped")
            continue
        rows.append(
            PsychosocialObservation(
                id=uuid.uuid4(),
                user_id=user_id,
                correlation_id=correlation_id,
                trace_id=trace.id,
                source_type=source_type,
                chat_message_id=source_id if source_type == "chat_message" else None,
                diary_entry_id=source_id if source_type == "diary_entry" else None,
                domain=item.domain,
                category=item.category,
                valence=item.valence,
                intensity=item.intensity,
                confidence=item.confidence,
                is_change=item.is_change,
                summary=item.summary.strip()[:MAX_SUMMARY_CHARS],
                evidence_quote=quote,
                status="inferred",
                observed_at=when,
                created_at=datetime.utcnow(),
            )
        )

    try:
        agent2_trace.mark_succeeded(trace, provider_result.metadata)
        db.add(trace)
        for row in rows:
            db.add(row)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        agent2_trace.mark_failed(
            db,
            trace,
            StructuredAnalysisError("provider_error", error_code="result_persistence_failed"),
        )
        logger.error("Agent 4 result could not be persisted")
        return ExtractionOutcome(trace.id, trace.status)

    return ExtractionOutcome(trace.id, "succeeded", [row.id for row in rows])


# -------------------------------------------------- deterministic scoring ---
@dataclass
class DomainState:
    domain: str
    label: str
    category: str
    category_label: str
    valence: str
    intensity: float
    confidence: float
    status: str
    summary: str
    quote: str
    observed_at: datetime
    observation_id: uuid.UUID
    weight: float
    contribution: float
    is_change: bool


@dataclass
class PsychosocialAssessment:
    """Everything the deterministic layer derives from stored observations."""

    index: float | None
    band: str
    domains: list[DomainState]
    risk_domains: list[str]
    protective_domains: list[str]
    acute_changes: list[DomainState]
    has_acute_change: bool
    observation_count: int
    active_count: int
    confirmed_count: int
    refuted_count: int
    computed_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "band": self.band,
            "risk_domains": self.risk_domains,
            "protective_domains": self.protective_domains,
            "has_acute_change": self.has_acute_change,
            "acute_change_categories": [state.category for state in self.acute_changes],
            "observation_count": self.observation_count,
            "active_count": self.active_count,
            "confirmed_count": self.confirmed_count,
            "refuted_count": self.refuted_count,
            "active_window_days": ACTIVE_WINDOW_DAYS,
            "acute_change_window_days": ACUTE_CHANGE_WINDOW_DAYS,
        }


def _band(index: float | None) -> str:
    if index is None:
        return "sin_datos"
    if index >= 0.60:
        return "alta"
    if index >= 0.35:
        return "moderada"
    return "baja"


# A confirmed observation is a human judgement and counts at full strength;
# an inferred one is discounted by how sure the model said it was.
STATUS_MULTIPLIER = {"confirmed": 1.0, "inferred": None, "refuted": 0.0}


def _effective_confidence(row: PsychosocialObservation) -> float:
    multiplier = STATUS_MULTIPLIER.get(row.status)
    if multiplier is not None:
        return multiplier
    return float(row.confidence)


def assess(db: Session, user_id, *, now: datetime | None = None) -> PsychosocialAssessment:
    """Fold stored observations into one inspectable vulnerability index.

    Per domain only the most recent non-refuted observation counts, so a
    situation that improved is not still being scored on its old state. The
    index is the weighted mean of adverse contributions minus the weighted
    mean of protective ones, clamped to 0..1.
    """
    now = now or datetime.utcnow()
    since = now - timedelta(days=ACTIVE_WINDOW_DAYS)

    rows = (
        db.query(PsychosocialObservation)
        .filter(
            PsychosocialObservation.user_id == user_id,
            PsychosocialObservation.observed_at >= since,
        )
        .order_by(PsychosocialObservation.observed_at.desc())
        .all()
    )
    total = (
        db.query(PsychosocialObservation)
        .filter(PsychosocialObservation.user_id == user_id)
        .count()
    )

    latest_by_domain: dict[str, PsychosocialObservation] = {}
    confirmed = 0
    refuted = 0
    for row in rows:
        if row.status == "confirmed":
            confirmed += 1
        if row.status == "refuted":
            refuted += 1
            continue
        latest_by_domain.setdefault(row.domain, row)

    domains: list[DomainState] = []
    risk_numerator = 0.0
    risk_denominator = 0.0
    protective_numerator = 0.0
    protective_denominator = 0.0

    for row in latest_by_domain.values():
        weight = DOMAIN_WEIGHTS.get(row.domain, 0.5)
        effective = _effective_confidence(row) * float(row.intensity)
        contribution = round(weight * effective, 4)
        if row.valence == "risk":
            risk_numerator += weight * effective
            risk_denominator += weight
        elif row.valence == "protective":
            protective_numerator += weight * effective
            protective_denominator += weight
        domains.append(
            DomainState(
                domain=row.domain,
                label=DOMAIN_LABELS.get(row.domain, row.domain),
                category=row.category,
                category_label=CATEGORY_LABELS.get(row.category, row.category),
                valence=row.valence,
                intensity=float(row.intensity),
                confidence=float(row.confidence),
                status=row.status,
                summary=row.summary,
                quote=row.evidence_quote,
                observed_at=row.observed_at,
                observation_id=row.id,
                weight=weight,
                contribution=contribution,
                is_change=bool(row.is_change),
            )
        )

    if risk_denominator == 0 and protective_denominator == 0:
        index: float | None = None
    else:
        adverse = risk_numerator / risk_denominator if risk_denominator else 0.0
        protective = protective_numerator / protective_denominator if protective_denominator else 0.0
        # Protection offsets adversity but never fully cancels it: someone
        # with strong support can still be losing their housing.
        index = max(0.0, min(1.0, adverse - 0.35 * protective))
        index = round(index, 3)

    acute_cutoff = now - timedelta(days=ACUTE_CHANGE_WINDOW_DAYS)
    acute = [
        state
        for state in domains
        if state.is_change
        and state.valence == "risk"
        and state.category in ACUTE_CHANGE_CATEGORIES
        and state.observed_at >= acute_cutoff
        and _effective_confidence_for(state) >= 0.5
    ]
    # Most recent first, and within the same moment the heaviest contributor
    # first. A single message often yields several changes at once, and the
    # panel leads with whichever one carries the most clinical weight rather
    # than whichever the model happened to list first.
    acute.sort(key=lambda state: (state.observed_at, state.contribution), reverse=True)

    domains.sort(key=lambda state: (state.valence != "risk", -state.contribution))

    return PsychosocialAssessment(
        index=index,
        band=_band(index),
        domains=domains,
        risk_domains=[state.domain for state in domains if state.valence == "risk"],
        protective_domains=[state.domain for state in domains if state.valence == "protective"],
        acute_changes=acute,
        has_acute_change=bool(acute),
        observation_count=total,
        active_count=len(domains),
        confirmed_count=confirmed,
        refuted_count=refuted,
        computed_at=now,
    )


def _effective_confidence_for(state: DomainState) -> float:
    multiplier = STATUS_MULTIPLIER.get(state.status)
    return multiplier if multiplier is not None else state.confidence


def adjudicate(
    db: Session,
    observation: PsychosocialObservation,
    *,
    status: str,
    actor_id,
    note: str | None = None,
) -> PsychosocialObservation:
    """Record a human judgement on an inferred observation.

    Confirming makes it count at full weight regardless of what the model's
    confidence was; refuting removes it from the index entirely. Only this
    function may change ``status`` — the extractor always writes ``inferred``.
    """
    if status not in ("confirmed", "refuted", "inferred"):
        raise ValueError("Unsupported adjudication status")
    observation.status = status
    observation.adjudicated_by = actor_id
    observation.adjudicated_at = datetime.utcnow()
    observation.adjudication_note = note
    db.commit()
    db.refresh(observation)
    return observation
