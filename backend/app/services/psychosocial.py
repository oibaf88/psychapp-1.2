"""
Agent 4 — psychosocial context extraction, and the deterministic indices
built from it.

Why this exists
---------------
A patient tells Agent 1, in passing, that their sister moved out, that the
landlord gave them a month, and that they gave their guitar to their nephew.
Nothing there is a suicidal statement, so Agent 2's linguistic flags stay
false and the structural score — which only ever reads check-ins — does not
move. The message scrolls away and the therapist never sees it. Read
together, those three sentences are the constellation that precedes a
crisis.

Two clearly separated halves
----------------------------
1. **Extraction (top half).** An LLM reads the patient's text and returns
   structured social determinants with a literal supporting quote. Like
   every model output in this codebase it is an *inference*, validated
   against a strict schema and stored as such; invalid output is discarded
   whole, and a quote that does not literally appear in the source text is
   dropped rather than shown.
2. **Scoring (bottom half).** Deterministic, inspectable arithmetic turns
   those stored observations into four indices and a set of flags. **No
   model is in this path.** The risk engine consumes only these numbers, so
   a hallucinated observation can move an index the clinician can see and
   audit, but it can never "decide" a level on its own — every rule that
   reads them requires convergence with an independent signal.

Four indices, not one
---------------------
An earlier version blended everything into a single vulnerability number.
That number was clinically unreadable: losing your flat and feeling like a
burden to your family are both "adversity", but they call for different
responses, and averaging them lets one mask the other. The four indices are
kept apart:

* ``support_index`` — how much real support is available (high is good).
* ``material_adversity_index`` — housing, money, work, access to care.
* ``interpersonal_risk_index`` — the two Interpersonal Theory of Suicide
  constructs (perceived burdensomeness, thwarted belongingness) plus
  withdrawal. Their *convergence* is what the level-4 rule looks for.
* ``relapse_context_index`` — the social environment around using.

Absence of data is not evidence of safety
-----------------------------------------
Every index is ``None``, never ``0.0``, when nothing is known, and the risk
engine records the rules that read it as *not evaluable* rather than *not
met*. A patient with no psychosocial data is assessed exactly as before this
module existed.

Observations do not expire; changes do
--------------------------------------
Losing your flat is still true next week, so the newest observation per
domain stays the current picture however old it is, flagged stale after
``STALE_AFTER_DAYS`` so nobody mistakes it for fresh. What *is* time-boxed is
acute change, the leave-taking signal, and "live" interpersonal risk — that
last window is what stops chronic adversity from re-raising the same alarm
every single day.

Clinical grounding of the weights
---------------------------------
The weights in ``app/content/psychosocial_catalog.py`` rank the determinants
that the suicide-prevention and addiction-relapse literature consistently
associates with proximal risk. They are deliberately coarse and are exposed
to the therapist in the panel and the manual, because a weight nobody can
see is a weight nobody can challenge. They are NOT a validated instrument
and must not be read as one.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.content.psychosocial_catalog import (
    ACUTE_CHANGE_CATEGORIES,
    CATEGORY_DOMAIN,
    CATEGORY_LABELS,
    DOMAIN_BY_KEY,
    DOMAIN_CATEGORIES,
    DOMAIN_KEYS,
    DOMAIN_LABELS,
    DOMAIN_WEIGHTS,
    GROUP_LABELS,
    INTERPERSONAL_DOMAINS,
    LEAVE_TAKING_DOMAIN,
    Domain,
    risk_value,
)
from app.models import PsychosocialObservation

logger = logging.getLogger("psychapp.psychosocial")

# The role its traces carried while it was a separate agent. Kept so the
# rows already in the database still name something the code knows about;
# new traces use the merged analyzer's role.
LEGACY_AGENT_ROLE = "agent4_psychosocial"
MAX_QUOTE_CHARS = 300
MAX_SUMMARY_CHARS = 400
MAX_OBSERVATIONS_PER_TEXT = 8

# Kept for the therapist panel and for historic comparability: how far back a
# reading is still treated as describing the present.
ACTIVE_WINDOW_DAYS = 90
# A "change" is only acute for a fortnight. This is the window in which the
# apparently-innocuous signals matter most.
ACUTE_CHANGE_WINDOW_DAYS = 14
# Past this the newest reading is still the current picture, but it is shown
# as stale: nobody has said anything about this domain in four months.
STALE_AFTER_DAYS = 120

# Below this an observation is displayed to the therapist but never scored.
# A hedged, ironic or third-hand mention should not move a threshold. This
# replaces the older behaviour of multiplying intensity by confidence, under
# which a 0.1-confidence guess still nudged every index it touched.
MIN_CONFIDENCE_FOR_SCORING = 0.50

# Very short texts ("ok", "gracias") cannot carry social context, so the
# psychosocial block of a merged analysis is not even looked at below this.
# It no longer saves a provider call — the linguistic read happens anyway —
# but it still avoids inventing observations out of two words.
MIN_TEXT_CHARS_FOR_EXTRACTION = 15

# Index thresholds. Named here, consumed by the risk engine, rendered in the
# panel, so a therapist reading "apoyo bajo" can find the number behind it.
SUPPORT_LOW_MAX = 0.34
SUPPORT_MODERATE_MAX = 0.60
MATERIAL_ADVERSITY_HIGH_MIN = 0.50
INTERPERSONAL_RISK_HIGH_MIN = 0.66
RELAPSE_CONTEXT_HIGH_MIN = 0.60

# Re-exported so existing callers keep working and so there is exactly one
# place these can be edited.
AGENT4_DOMAIN_CATEGORIES = DOMAIN_CATEGORIES

__all__ = [
    "ACTIVE_WINDOW_DAYS",
    "ACUTE_CHANGE_CATEGORIES",
    "ACUTE_CHANGE_WINDOW_DAYS",
    "CATEGORY_DOMAIN",
    "CATEGORY_LABELS",
    "DOMAIN_LABELS",
    "DOMAIN_WEIGHTS",
    "GROUP_LABELS",
    "INTERPERSONAL_RISK_HIGH_MIN",
    "MATERIAL_ADVERSITY_HIGH_MIN",
    "MIN_CONFIDENCE_FOR_SCORING",
    "RELAPSE_CONTEXT_HIGH_MIN",
    "STALE_AFTER_DAYS",
    "SUPPORT_LOW_MAX",
    "DomainState",
    "PsychosocialAssessment",
    "adjudicate",
    "assess",
    "build_observation_rows",
]


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


def _deduplicate(observations: list[PsychosocialObservationIn]) -> list[PsychosocialObservationIn]:
    """One observation per domain, highest confidence wins.

    The prompt asks for this; this enforces it, because two rows for one
    domain in a single extraction would make "the current reading" ambiguous
    for every reader downstream.
    """
    best: dict[str, PsychosocialObservationIn] = {}
    for item in observations:
        incumbent = best.get(item.domain)
        if incumbent is None or item.confidence > incumbent.confidence:
            best[item.domain] = item
    ordered = sorted(best.values(), key=lambda item: item.confidence, reverse=True)
    return ordered[:MAX_OBSERVATIONS_PER_TEXT]


def build_observation_rows(
    block: dict,
    *,
    user_id,
    text: str,
    source_type: str,
    source_id: uuid.UUID,
    correlation_id: uuid.UUID,
    trace_id: uuid.UUID,
    observed_at: datetime | None = None,
) -> list[PsychosocialObservation]:
    """Validate one psychosocial block and turn it into unsaved rows.

    Split out of the provider call so the merged analyzer can hand over a
    block it already has. Everything that made the extraction trustworthy
    stays here and stays in the same order: strict validation, one
    observation per domain, the domain/category coherence check, and the
    requirement that the quote appear verbatim in the patient's own text.

    Raises ``ValidationError`` when the block does not satisfy the schema.
    The caller decides what that costs — under the merged analyzer it costs
    the psychosocial half only, not the linguistic one.
    """
    extraction = PsychosocialExtraction.model_validate(block)
    when = observed_at or datetime.utcnow()
    rows: list[PsychosocialObservation] = []
    for item in _deduplicate(extraction.observations):
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
                trace_id=trace_id,
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

    return rows


# -------------------------------------------------- deterministic scoring ---
@dataclass
class DomainState:
    domain: str
    label: str
    group: str
    group_label: str
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
    age_days: float
    risk_value: float
    counts_for_scoring: bool
    is_stale: bool
    is_recent_change: bool
    has_pending_update: bool
    session_question: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "label": self.label,
            "group": self.group,
            "group_label": self.group_label,
            "category": self.category,
            "category_label": self.category_label,
            "valence": self.valence,
            "intensity": self.intensity,
            "confidence": self.confidence,
            "status": self.status,
            "summary": self.summary,
            "quote": self.quote,
            "observation_id": str(self.observation_id),
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "age_days": self.age_days,
            "weight": self.weight,
            "contribution": self.contribution,
            "risk_value": self.risk_value,
            "is_change": self.is_change,
            "is_recent_change": self.is_recent_change,
            "is_stale": self.is_stale,
            "counts_for_scoring": self.counts_for_scoring,
            "has_pending_update": self.has_pending_update,
        }


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
    # --- the four separate indices ---------------------------------------
    support_index: float | None = None
    material_adversity_index: float | None = None
    interpersonal_risk_index: float | None = None
    relapse_context_index: float | None = None
    scored_count: int = 0
    stale_domains: list[str] = field(default_factory=list)
    pending_update_domains: list[str] = field(default_factory=list)
    interpersonal_recent_evidence: list[str] = field(default_factory=list)
    leave_taking: DomainState | None = None

    # ---- predicates the risk engine asks for, so thresholds live here ----
    @property
    def available(self) -> bool:
        return bool(self.domains)

    @property
    def support_is_low(self) -> bool | None:
        if self.support_index is None:
            return None
        return self.support_index <= SUPPORT_LOW_MAX

    @property
    def material_adversity_is_high(self) -> bool | None:
        if self.material_adversity_index is None:
            return None
        return self.material_adversity_index >= MATERIAL_ADVERSITY_HIGH_MIN

    @property
    def interpersonal_risk_is_high(self) -> bool | None:
        if self.interpersonal_risk_index is None:
            return None
        return self.interpersonal_risk_index >= INTERPERSONAL_RISK_HIGH_MIN

    @property
    def relapse_context_is_high(self) -> bool | None:
        if self.relapse_context_index is None:
            return None
        return self.relapse_context_index >= RELAPSE_CONTEXT_HIGH_MIN

    @property
    def interpersonal_risk_is_live(self) -> bool:
        """High interpersonal risk the patient has voiced recently.

        Without this, a chronic "nobody needs me" recorded months ago would
        re-raise the same alarm every time an earlier alert was closed. The
        rules that use it therefore ask for both: a high index AND something
        said in the last two weeks.
        """
        return bool(self.interpersonal_risk_is_high) and bool(self.interpersonal_recent_evidence)

    @property
    def has_leave_taking_signal(self) -> bool:
        return self.leave_taking is not None

    def as_dict(self) -> dict[str, Any]:
        """Snapshot stored verbatim inside the risk engine's calculation trace."""
        return {
            "index": self.index,
            "band": self.band,
            "available": self.available,
            "indices": {
                "support_index": self.support_index,
                "material_adversity_index": self.material_adversity_index,
                "interpersonal_risk_index": self.interpersonal_risk_index,
                "relapse_context_index": self.relapse_context_index,
            },
            "thresholds": {
                "min_confidence_for_scoring": MIN_CONFIDENCE_FOR_SCORING,
                "support_low_max": SUPPORT_LOW_MAX,
                "material_adversity_high_min": MATERIAL_ADVERSITY_HIGH_MIN,
                "interpersonal_risk_high_min": INTERPERSONAL_RISK_HIGH_MIN,
                "relapse_context_high_min": RELAPSE_CONTEXT_HIGH_MIN,
                "acute_change_window_days": ACUTE_CHANGE_WINDOW_DAYS,
                "stale_after_days": STALE_AFTER_DAYS,
            },
            "formulas": {
                "support_index": "1 - weighted_mean(risk_value, support_weight)",
                "material_adversity_index": "weighted_mean(risk_value, material_weight)",
                "interpersonal_risk_index": "weighted_mean(risk_value, interpersonal_weight)",
                "relapse_context_index": "weighted_mean(risk_value, relapse_weight)",
                "risk_value_scale": "protective=0.0, neutral=0.25, risk=intensity",
            },
            "risk_domains": self.risk_domains,
            "protective_domains": self.protective_domains,
            "has_acute_change": self.has_acute_change,
            "acute_change_categories": [state.category for state in self.acute_changes],
            "acute_change_domains": [state.domain for state in self.acute_changes],
            "interpersonal_recent_evidence": self.interpersonal_recent_evidence,
            "leave_taking": self.leave_taking.as_dict() if self.leave_taking else None,
            "stale_domains": self.stale_domains,
            "pending_update_domains": self.pending_update_domains,
            "observation_count": self.observation_count,
            "active_count": self.active_count,
            "scored_count": self.scored_count,
            "confirmed_count": self.confirmed_count,
            "refuted_count": self.refuted_count,
            "active_window_days": ACTIVE_WINDOW_DAYS,
            "acute_change_window_days": ACUTE_CHANGE_WINDOW_DAYS,
            "domains": [state.as_dict() for state in self.domains],
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


def _effective_confidence(row) -> float:
    multiplier = STATUS_MULTIPLIER.get(row.status)
    if multiplier is not None:
        return multiplier
    return float(row.confidence)


def _weighted_index(
    states: list[DomainState],
    weight_of: Callable[[Domain], float],
) -> float | None:
    """Weighted mean of risk values over the domains that carry a weight.

    Returns None — not 0.0 — when nothing is known, so "no data" can never be
    mistaken for "no adversity" by a threshold comparison.
    """
    total_weight = 0.0
    accumulated = 0.0
    for state in states:
        if not state.counts_for_scoring:
            continue
        domain = DOMAIN_BY_KEY.get(state.domain)
        if domain is None:
            continue
        weight = weight_of(domain)
        if weight <= 0:
            continue
        total_weight += weight
        accumulated += weight * state.risk_value
    if total_weight == 0:
        return None
    return round(accumulated / total_weight, 3)


def assess(db: Session, user_id, *, now: datetime | None = None) -> PsychosocialAssessment:
    """Fold stored observations into inspectable indices.

    Per domain only the most recent non-refuted observation counts, so a
    situation that improved is not still scored on its old state — with one
    exception that is the whole point of the fact/inference wall: a domain a
    professional has *confirmed* is not silently overwritten by a later model
    reading. The newer inference is kept and reported as a pending update for
    the professional to accept or reject.
    """
    now = now or datetime.utcnow()

    rows = (
        db.query(PsychosocialObservation)
        .filter(PsychosocialObservation.user_id == user_id)
        .order_by(PsychosocialObservation.observed_at.desc())
        .all()
    )
    total = len(rows)

    # Rows arrive newest first. Per domain the winner is the newest
    # non-refuted row, except that a confirmed row outranks any inference,
    # however recent.
    current: dict[str, Any] = {}
    pending_updates: set[str] = set()
    confirmed = 0
    refuted = 0
    for row in rows:
        if row.status == "confirmed":
            confirmed += 1
        if row.status == "refuted":
            refuted += 1
            continue
        if row.domain not in DOMAIN_BY_KEY:
            # A domain retired from the catalogue: keep it out of the index
            # rather than scoring it with a guessed weight.
            continue
        incumbent = current.get(row.domain)
        if incumbent is None:
            current[row.domain] = row
            continue
        if incumbent.status == "confirmed" and row.status != "confirmed":
            continue
        if row.status == "confirmed" and incumbent.status != "confirmed":
            # The confirmed row is older (rows are newest first), so the
            # inference we already saw is a newer, unreviewed reading.
            pending_updates.add(row.domain)
            current[row.domain] = row

    acute_cutoff = now - timedelta(days=ACUTE_CHANGE_WINDOW_DAYS)
    stale_cutoff = now - timedelta(days=STALE_AFTER_DAYS)

    domains: list[DomainState] = []
    risk_numerator = 0.0
    risk_denominator = 0.0
    protective_numerator = 0.0
    protective_denominator = 0.0

    for row in current.values():
        catalog_domain = DOMAIN_BY_KEY[row.domain]
        weight = DOMAIN_WEIGHTS.get(row.domain, 0.5)
        effective_confidence = _effective_confidence(row)
        # A human declaration always scores; a model reading has to clear the
        # confidence floor before it may move any threshold.
        counts = row.status == "confirmed" or float(row.confidence) >= MIN_CONFIDENCE_FOR_SCORING
        value = risk_value(row.valence, float(row.intensity))
        legacy_effective = effective_confidence * float(row.intensity)
        contribution = round(weight * legacy_effective, 4)
        if row.valence == "risk":
            risk_numerator += weight * legacy_effective
            risk_denominator += weight
        elif row.valence == "protective":
            protective_numerator += weight * legacy_effective
            protective_denominator += weight
        observed_at = row.observed_at
        age_days = round((now - observed_at).total_seconds() / 86400.0, 2) if observed_at else 0.0
        domains.append(
            DomainState(
                domain=row.domain,
                label=DOMAIN_LABELS.get(row.domain, row.domain),
                group=catalog_domain.group,
                group_label=GROUP_LABELS.get(catalog_domain.group, catalog_domain.group),
                category=row.category,
                category_label=CATEGORY_LABELS.get(row.category, row.category),
                valence=row.valence,
                intensity=float(row.intensity),
                confidence=float(row.confidence),
                status=row.status,
                summary=row.summary,
                quote=row.evidence_quote,
                observed_at=observed_at,
                observation_id=row.id,
                weight=weight,
                contribution=contribution,
                is_change=bool(row.is_change),
                age_days=age_days,
                risk_value=value,
                counts_for_scoring=counts,
                is_stale=bool(observed_at and observed_at < stale_cutoff),
                is_recent_change=bool(row.is_change and observed_at and observed_at >= acute_cutoff),
                has_pending_update=row.domain in pending_updates,
                session_question=catalog_domain.session_question,
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

    support_risk = _weighted_index(domains, lambda d: d.support_weight)
    support_index = None if support_risk is None else round(1.0 - support_risk, 3)

    acute = [
        state
        for state in domains
        if state.is_change
        and state.valence == "risk"
        and state.category in ACUTE_CHANGE_CATEGORIES
        and state.observed_at >= acute_cutoff
        and state.counts_for_scoring
    ]
    # Most recent first, and within the same moment the heaviest contributor
    # first. A single message often yields several changes at once, and the
    # panel leads with whichever one carries the most clinical weight rather
    # than whichever the model happened to list first.
    acute.sort(key=lambda state: (state.observed_at, state.contribution), reverse=True)

    # The leave-taking signal is only "live" while it is recent: giving a
    # guitar away three months ago is history, not a warning.
    leave_taking = next(
        (
            state
            for state in domains
            if state.domain == LEAVE_TAKING_DOMAIN
            and state.valence == "risk"
            and state.counts_for_scoring
            and state.observed_at >= acute_cutoff
        ),
        None,
    )

    interpersonal_recent = sorted(
        state.domain
        for state in domains
        if state.domain in INTERPERSONAL_DOMAINS
        and state.valence == "risk"
        and state.counts_for_scoring
        and state.observed_at >= acute_cutoff
    )

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
        support_index=support_index,
        material_adversity_index=_weighted_index(domains, lambda d: d.material_weight),
        interpersonal_risk_index=_weighted_index(domains, lambda d: d.interpersonal_weight),
        relapse_context_index=_weighted_index(domains, lambda d: d.relapse_weight),
        scored_count=sum(1 for state in domains if state.counts_for_scoring),
        stale_domains=sorted(state.domain for state in domains if state.is_stale),
        pending_update_domains=sorted(pending_updates),
        interpersonal_recent_evidence=interpersonal_recent,
        leave_taking=leave_taking,
    )


def suggested_session_questions(assessment: PsychosocialAssessment, *, limit: int = 5) -> list[dict[str, str]]:
    """Questions to bring to the next session, from what is actually moving.

    Ordered by what the deterministic layer is currently weighting most:
    leave-taking first because it is the one that cannot wait, then the
    interpersonal constructs, then whatever changed in the last fortnight.
    """
    questions: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(state: DomainState, reason: str) -> None:
        if state.domain in seen or not state.session_question:
            return
        seen.add(state.domain)
        questions.append(
            {
                "domain": state.domain,
                "domain_label": state.label,
                "question": state.session_question,
                "reason": reason,
                "quote": state.quote,
            }
        )

    if assessment.leave_taking is not None:
        add(assessment.leave_taking, "Señal de despedida registrada en los últimos 14 días")
    for state in assessment.domains:
        if state.domain in INTERPERSONAL_DOMAINS and state.valence == "risk" and state.counts_for_scoring:
            add(state, "Constructo de riesgo interpersonal activo")
    for state in assessment.acute_changes:
        add(state, "Cambio adverso reciente")
    for state in assessment.domains:
        if len(questions) >= limit:
            break
        if state.valence == "risk" and state.counts_for_scoring:
            add(state, "Dominio en adversidad")

    return questions[:limit]


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
    confidence was, and pins the domain against later inferences; refuting
    removes it from the indices entirely. Only this function may change
    ``status`` — the extractor always writes ``inferred``.
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
