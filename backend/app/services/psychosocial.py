"""
Agent 4 — psychosocial context: extraction, storage and deterministic index.

The problem this module solves
------------------------------
A patient tells Agent 1, in passing, that their sister moved out, that the
landlord gave them a month, and that they gave their guitar to their nephew.
Nothing in that message is a suicidal statement, so Agent 2's linguistic
flags stay false and the structural score — which only ever looks at
check-ins — stays exactly where it was. The message scrolls away and the
therapist never sees it.

That is the class of signal this module exists to keep: the social ground a
person is standing on, and the small, apparently innocuous markers that only
mean something once you count them together.

How it works, and where the boundaries are
------------------------------------------
1.  **Extraction (LLM, inference).** Agent 4 reads the same text Agent 2
    reads and returns one structured observation per psychosocial domain,
    each with the literal quote that produced it. Strictly validated at the
    boundary; anything unexpected is dropped, never coerced.

2.  **Storage (per domain, superseding).** A psychosocial situation persists
    — losing your flat is still true next week — so observations are not
    given a freshness window like linguistic signals. The newest observation
    per domain becomes the current picture and supersedes the previous one,
    keeping the full history addressable.

3.  **The fact wall holds.** If a professional confirmed a domain, that row
    stays authoritative: a later Agent 4 inference is stored and surfaced as
    a *pending update* for the professional to accept or reject, but it does
    not silently overwrite a confirmed situation.

4.  **Scoring (deterministic, no LLM).** ``build_profile`` turns the current
    observations into four indices under fixed weights and thresholds. This
    is ordinary arithmetic over table rows; it is reproducible, and the risk
    engine records the whole computation in its calculation trace.

5.  **Deciding (risk engine only).** Nothing here returns an alert level.
    ``app/services/risk_engine.py`` compares these indices against fixed
    thresholds in the same ordered rule list as everything else.

Clinical model
--------------
The interpersonal indices follow the two constructs of the Interpersonal
Theory of Suicide — perceived burdensomeness and thwarted belongingness —
because their convergence, plus leave-taking behaviour, is precisely the
constellation that reads as harmless message by message. The material and
relational indices follow the social-determinants framing the project docs
use for "contexto de apoyo y social".
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.content.prompts import AGENT4_SYSTEM_PROMPT, AGENT4_TOOL_SCHEMA
from app.content.psychosocial_catalog import (
    ACUTE_RUPTURE_DOMAINS,
    DIRECTIONS,
    DIRECTION_WORSENING,
    DOMAIN_BY_KEY,
    DOMAIN_KEYS,
    INTERPERSONAL_DOMAINS,
    LEAVE_TAKING_DOMAIN,
    ONSETS,
    STATE_MILD,
    STATE_MODERATE,
    STATE_PROTECTIVE,
    STATE_RISK_VALUE,
    STATES,
    Domain,
    state_at_least,
)
from app.models import ConfirmedFact, PsychosocialObservation
from app.services import psychosocial_trace
from app.services.llm import StructuredAnalysisError, get_llm_provider

logger = logging.getLogger("psychapp.psychosocial")

# ------------------------------------------------------------- constants ---
# Below this, an observation is shown to the therapist but never scored: a
# hedged, ironic or third-hand mention should not move a threshold.
MIN_CONFIDENCE_FOR_SCORING = 0.50

# What counts as "this just happened" for the acute-rupture rules.
RECENT_CHANGE_DAYS = 14

# Past this, the situation is still displayed as the last thing known but is
# flagged stale, because nobody has said anything about it in four months.
STALE_AFTER_DAYS = 120

# Index thresholds. Named here, consumed by the risk engine, rendered in the
# panel, so a therapist reading "apoyo bajo" can find the number behind it.
SUPPORT_LOW_MAX = 0.34
SUPPORT_MODERATE_MAX = 0.60
MATERIAL_ADVERSITY_HIGH_MIN = 0.50
INTERPERSONAL_RISK_HIGH_MIN = 0.66
RELAPSE_CONTEXT_HIGH_MIN = 0.60

# Agent 4 costs one extra provider call per patient message. Very short texts
# ("ok", "gracias") cannot carry social context worth the round trip.
MIN_TEXT_CHARS_FOR_EXTRACTION = 15

MAX_OBSERVATIONS_PER_TEXT = 8

PSYCHOSOCIAL_FACT_CATEGORY = "psychosocial_context"


# ------------------------------------------------- model output boundary ---
class PsychosocialObservationIn(BaseModel):
    """One domain reading as returned by Agent 4.

    ``strict`` plus ``extra="forbid"`` keeps the model on the contract: an
    invented domain, a score outside 0-1 or a helpfully added field fails the
    whole extraction rather than reaching the database half-understood.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    domain: str
    state: Literal["protector", "neutro", "riesgo_leve", "riesgo_moderado", "riesgo_alto"]
    direction: Literal["mejora", "estable", "empeora", "desconocido"]
    onset: Literal["reciente", "cronico", "desconocido"]
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=400)
    evidence_quote: str = Field(min_length=1, max_length=600)

    @field_validator("domain")
    @classmethod
    def _known_domain(cls, value: str) -> str:
        if value not in DOMAIN_KEYS:
            raise ValueError(f"unknown psychosocial domain: {value}")
        return value


class PsychosocialExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    has_psychosocial_content: bool
    overall_note: str = Field(min_length=1, max_length=600)
    observations: list[PsychosocialObservationIn] = Field(default_factory=list, max_length=32)


@dataclass(frozen=True)
class ExtractionOutcome:
    correlation_id: uuid.UUID
    trace_id: uuid.UUID | None
    status: str
    observation_ids: list[uuid.UUID] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    note: str | None = None


# --------------------------------------------------------------- helpers ---
def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _utc_iso(value: datetime | None) -> str | None:
    aware = _utc(value)
    return aware.astimezone(timezone.utc).isoformat() if aware else None


def _age_days(value: datetime | None, now: datetime) -> float | None:
    aware = _utc(value)
    if aware is None:
        return None
    return round((_utc(now) - aware).total_seconds() / 86400.0, 2)


# --------------------------------------------------------- current state ---
@dataclass(frozen=True)
class DomainState:
    """The current reading for one domain, plus everything needed to audit it."""

    domain: str
    label: str
    group: str
    state: str
    direction: str
    onset: str
    confidence: float
    summary: str
    evidence_quote: str | None
    observation_id: str | None
    source_type: str | None
    source_id: str | None
    recorded_by: str
    is_declared: bool
    observed_at: str | None
    age_days: float | None
    is_recent_change: bool
    is_stale: bool
    counts_for_scoring: bool
    risk_value: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "label": self.label,
            "group": self.group,
            "state": self.state,
            "direction": self.direction,
            "onset": self.onset,
            "confidence": self.confidence,
            "summary": self.summary,
            "evidence_quote": self.evidence_quote,
            "observation_id": self.observation_id,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "recorded_by": self.recorded_by,
            "is_declared": self.is_declared,
            "observed_at": self.observed_at,
            "age_days": self.age_days,
            "is_recent_change": self.is_recent_change,
            "is_stale": self.is_stale,
            "counts_for_scoring": self.counts_for_scoring,
            "risk_value": self.risk_value,
        }


@dataclass(frozen=True)
class PsychosocialProfile:
    """Deterministic summary of a patient's social context at one instant."""

    available: bool
    evaluated_at: str
    domains: dict[str, DomainState]
    support_index: float | None
    material_adversity_index: float | None
    interpersonal_risk_index: float | None
    relapse_context_index: float | None
    scored_domain_count: int
    known_domain_count: int
    acute_deterioration: list[str]
    leave_taking: DomainState | None
    risk_domains: list[str]
    protective_domains: list[str]
    stale_domains: list[str]
    pending_update_domains: list[str]
    interpersonal_recent_evidence: list[str]

    # ---- predicates the risk engine asks for, so the thresholds live here --
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
    def has_acute_rupture(self) -> bool:
        return bool(self.acute_deterioration)

    @property
    def interpersonal_risk_is_live(self) -> bool:
        """High interpersonal risk that the patient has voiced recently.

        Without this, a chronic "nobody needs me" recorded months ago would
        keep re-raising an alarm every time an earlier alert is closed. The
        rule that uses it therefore asks for both: a high index AND something
        said in the last two weeks.
        """
        return bool(self.interpersonal_risk_is_high) and bool(self.interpersonal_recent_evidence)

    @property
    def has_leave_taking_signal(self) -> bool:
        return self.leave_taking is not None

    def as_dict(self) -> dict[str, Any]:
        """Snapshot stored verbatim inside the risk engine's calculation trace."""
        return {
            "available": self.available,
            "evaluated_at": self.evaluated_at,
            "thresholds": {
                "min_confidence_for_scoring": MIN_CONFIDENCE_FOR_SCORING,
                "recent_change_days": RECENT_CHANGE_DAYS,
                "stale_after_days": STALE_AFTER_DAYS,
                "support_low_max": SUPPORT_LOW_MAX,
                "material_adversity_high_min": MATERIAL_ADVERSITY_HIGH_MIN,
                "interpersonal_risk_high_min": INTERPERSONAL_RISK_HIGH_MIN,
                "relapse_context_high_min": RELAPSE_CONTEXT_HIGH_MIN,
            },
            "indices": {
                "support_index": self.support_index,
                "material_adversity_index": self.material_adversity_index,
                "interpersonal_risk_index": self.interpersonal_risk_index,
                "relapse_context_index": self.relapse_context_index,
            },
            "formulas": {
                "support_index": "1 - weighted_mean(risk_value, support_weight)",
                "material_adversity_index": "weighted_mean(risk_value, material_weight)",
                "interpersonal_risk_index": "weighted_mean(risk_value, interpersonal_weight)",
                "relapse_context_index": "weighted_mean(risk_value, relapse_weight)",
                "risk_value_scale": STATE_RISK_VALUE,
            },
            "known_domain_count": self.known_domain_count,
            "scored_domain_count": self.scored_domain_count,
            "acute_deterioration": self.acute_deterioration,
            "leave_taking": self.leave_taking.as_dict() if self.leave_taking else None,
            "risk_domains": self.risk_domains,
            "protective_domains": self.protective_domains,
            "stale_domains": self.stale_domains,
            "pending_update_domains": self.pending_update_domains,
            "interpersonal_recent_evidence": self.interpersonal_recent_evidence,
            "domains": {key: value.as_dict() for key, value in self.domains.items()},
        }


def _empty_profile(now: datetime) -> PsychosocialProfile:
    return PsychosocialProfile(
        available=False,
        evaluated_at=_utc_iso(now) or "",
        domains={},
        support_index=None,
        material_adversity_index=None,
        interpersonal_risk_index=None,
        relapse_context_index=None,
        scored_domain_count=0,
        known_domain_count=0,
        acute_deterioration=[],
        leave_taking=None,
        risk_domains=[],
        protective_domains=[],
        stale_domains=[],
        pending_update_domains=[],
        interpersonal_recent_evidence=[],
    )


def _weighted_index(
    states: dict[str, DomainState],
    weight_of,
) -> float | None:
    """Weighted mean of risk values over the domains that carry a weight.

    Returns None — not 0.0 — when nothing is known, so "no data" can never be
    mistaken for "no adversity" by a threshold comparison.
    """
    total_weight = 0.0
    accumulated = 0.0
    for key, domain_state in states.items():
        if not domain_state.counts_for_scoring or domain_state.risk_value is None:
            continue
        domain = DOMAIN_BY_KEY.get(key)
        if domain is None:
            continue
        weight = weight_of(domain)
        if weight <= 0:
            continue
        total_weight += weight
        accumulated += weight * domain_state.risk_value
    if total_weight == 0:
        return None
    return round(accumulated / total_weight, 3)


def build_profile(
    observations: list[PsychosocialObservation],
    *,
    now: datetime | None = None,
) -> PsychosocialProfile:
    """Deterministically fold current observations into indices and flags.

    Pure function over rows: same input, same output, no clock reads beyond
    ``now`` and no database access. The risk engine stores the result inside
    the assessment, so a historic decision can be re-read exactly as it was
    taken.
    """
    now = now or datetime.utcnow()
    if not observations:
        return _empty_profile(now)

    # Per domain: a professional declaration wins over an inference; among
    # equals, the most recently observed row wins.
    by_domain: dict[str, PsychosocialObservation] = {}
    pending_updates: set[str] = set()
    for row in observations:
        if row.domain not in DOMAIN_BY_KEY:
            continue
        incumbent = by_domain.get(row.domain)
        if incumbent is None:
            by_domain[row.domain] = row
            continue
        incumbent_declared = _is_declared(incumbent)
        challenger_declared = _is_declared(row)
        if challenger_declared and not incumbent_declared:
            if _observed(incumbent) > _observed(row):
                pending_updates.add(row.domain)
            by_domain[row.domain] = row
        elif incumbent_declared and not challenger_declared:
            # A newer inference contradicting a confirmed domain is kept
            # visible for the professional instead of being applied silently.
            if _observed(row) > _observed(incumbent):
                pending_updates.add(row.domain)
        elif _observed(row) > _observed(incumbent):
            by_domain[row.domain] = row

    states: dict[str, DomainState] = {}
    for key, row in by_domain.items():
        domain = DOMAIN_BY_KEY[key]
        age = _age_days(row.observed_at, now)
        declared = _is_declared(row)
        confidence = float(row.confidence or 0.0)
        counts = declared or confidence >= MIN_CONFIDENCE_FOR_SCORING
        states[key] = DomainState(
            domain=key,
            label=domain.label,
            group=domain.group,
            state=row.state,
            direction=row.direction,
            onset=row.onset,
            confidence=round(confidence, 3),
            summary=row.summary,
            evidence_quote=row.evidence_quote,
            observation_id=str(row.id) if row.id else None,
            source_type=row.source_type,
            source_id=str(row.source_id) if row.source_id else None,
            recorded_by=row.recorded_by,
            is_declared=declared,
            observed_at=_utc_iso(row.observed_at),
            age_days=age,
            is_recent_change=age is not None and age <= RECENT_CHANGE_DAYS,
            is_stale=age is not None and age > STALE_AFTER_DAYS,
            counts_for_scoring=counts,
            risk_value=STATE_RISK_VALUE.get(row.state),
        )

    if not states:
        return _empty_profile(now)

    support_risk = _weighted_index(states, lambda d: d.support_weight)
    support_index = None if support_risk is None else round(1.0 - support_risk, 3)

    acute = sorted(
        key
        for key, value in states.items()
        if key in ACUTE_RUPTURE_DOMAINS
        and value.counts_for_scoring
        and value.is_recent_change
        and value.direction == DIRECTION_WORSENING
        and state_at_least(value.state, STATE_MODERATE)
    )

    leave_taking = states.get(LEAVE_TAKING_DOMAIN)
    if leave_taking is not None and not (
        leave_taking.counts_for_scoring
        and leave_taking.is_recent_change
        and state_at_least(leave_taking.state, STATE_MILD)
    ):
        leave_taking = None

    return PsychosocialProfile(
        available=True,
        evaluated_at=_utc_iso(now) or "",
        domains=dict(sorted(states.items())),
        support_index=support_index,
        material_adversity_index=_weighted_index(states, lambda d: d.material_weight),
        interpersonal_risk_index=_weighted_index(states, lambda d: d.interpersonal_weight),
        relapse_context_index=_weighted_index(states, lambda d: d.relapse_weight),
        scored_domain_count=sum(1 for value in states.values() if value.counts_for_scoring),
        known_domain_count=len(states),
        acute_deterioration=acute,
        leave_taking=leave_taking,
        risk_domains=sorted(key for key, value in states.items() if state_at_least(value.state, STATE_MILD)),
        protective_domains=sorted(key for key, value in states.items() if value.state == STATE_PROTECTIVE),
        stale_domains=sorted(key for key, value in states.items() if value.is_stale),
        pending_update_domains=sorted(pending_updates),
        interpersonal_recent_evidence=sorted(
            key
            for key, value in states.items()
            if key in INTERPERSONAL_DOMAINS
            and value.counts_for_scoring
            and value.is_recent_change
            and state_at_least(value.state, STATE_MODERATE)
        ),
    )


def _is_declared(row: PsychosocialObservation) -> bool:
    """A row a human put their name to, rather than a model reading."""
    return bool(row.confirmed_fact_id) or row.recorded_by in ("professional", "user")


def _observed(row: PsychosocialObservation) -> datetime:
    return _utc(row.observed_at) or _utc(row.created_at) or datetime.min.replace(tzinfo=timezone.utc)


def current_observations(db: Session, user_id) -> list[PsychosocialObservation]:
    return (
        db.query(PsychosocialObservation)
        .filter(
            PsychosocialObservation.user_id == user_id,
            PsychosocialObservation.is_current == True,  # noqa: E712
            PsychosocialObservation.dismissed_at.is_(None),
        )
        .order_by(PsychosocialObservation.observed_at.asc())
        .all()
    )


def current_profile(db: Session, user_id, *, now: datetime | None = None) -> PsychosocialProfile:
    return build_profile(current_observations(db, user_id), now=now)


def history(db: Session, user_id, *, limit: int = 200) -> list[PsychosocialObservation]:
    return (
        db.query(PsychosocialObservation)
        .filter(PsychosocialObservation.user_id == user_id)
        .order_by(PsychosocialObservation.observed_at.desc())
        .limit(min(limit, 500))
        .all()
    )


# ------------------------------------------------------------ extraction ---
def _deduplicate(observations: list[PsychosocialObservationIn]) -> list[PsychosocialObservationIn]:
    """One observation per domain, highest confidence wins.

    The prompt forbids duplicates; this enforces it rather than trusting it,
    because two rows for one domain would make "the current picture"
    ambiguous for every reader downstream.
    """
    best: dict[str, PsychosocialObservationIn] = {}
    for item in observations:
        incumbent = best.get(item.domain)
        if incumbent is None or item.confidence > incumbent.confidence:
            best[item.domain] = item
    ordered = sorted(best.values(), key=lambda item: item.confidence, reverse=True)
    return ordered[:MAX_OBSERVATIONS_PER_TEXT]


def _supersede_current(db: Session, user_id, domain: str, replacement_id: uuid.UUID) -> None:
    """Retire the previous inference for this domain.

    Confirmed rows are left alone on purpose: see the fact-wall note in the
    module docstring. The new row still lands, and ``build_profile`` reports
    the domain as having a pending update.
    """
    rows = (
        db.query(PsychosocialObservation)
        .filter(
            PsychosocialObservation.user_id == user_id,
            PsychosocialObservation.domain == domain,
            PsychosocialObservation.is_current == True,  # noqa: E712
            PsychosocialObservation.id != replacement_id,
            PsychosocialObservation.confirmed_fact_id.is_(None),
        )
        .all()
    )
    for row in rows:
        row.is_current = False
        row.superseded_by = replacement_id


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
    """Run Agent 4 over one patient text and persist what it found.

    Never raises into the patient-facing flow. Every failure mode — trace not
    persistable, provider down, refusal, malformed output, write error —
    returns an outcome and leaves the deterministic engine to run on whatever
    psychosocial context was already known.
    """
    if not text or len(text.strip()) < MIN_TEXT_CHARS_FOR_EXTRACTION:
        return ExtractionOutcome(correlation_id, None, "skipped_short_text")

    try:
        trace = psychosocial_trace.start(
            db,
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
            correlation_id=correlation_id,
        )
    except psychosocial_trace.TracePersistenceError:
        logger.error("Agent 4 skipped because its trace could not be persisted")
        return ExtractionOutcome(correlation_id, None, "trace_persistence_error")

    try:
        provider_result = get_llm_provider().analyze_structured(
            AGENT4_SYSTEM_PROMPT,
            text,
            AGENT4_TOOL_SCHEMA,
        )
        extraction = PsychosocialExtraction.model_validate(provider_result.value)
    except Exception as exc:  # noqa: BLE001
        psychosocial_trace.mark_failed(db, trace, exc)
        logger.error("Agent 4 extraction failed safely: %s", type(exc).__name__)
        return ExtractionOutcome(correlation_id, trace.id, trace.status)

    selected = _deduplicate(extraction.observations)
    stored: list[PsychosocialObservation] = []
    now = observed_at or datetime.utcnow()
    for item in selected:
        stored.append(
            PsychosocialObservation(
                id=uuid.uuid4(),
                user_id=user_id,
                domain=item.domain,
                state=item.state,
                direction=item.direction,
                onset=item.onset,
                confidence=item.confidence,
                summary=item.summary,
                evidence_quote=item.evidence_quote,
                source_type=source_type,
                source_id=source_id,
                extraction_trace_id=trace.id,
                correlation_id=correlation_id,
                recorded_by="agent4",
                is_current=True,
                observed_at=now,
            )
        )

    try:
        psychosocial_trace.mark_succeeded(trace, provider_result.metadata, observation_count=len(stored))
        db.add(trace)
        for row in stored:
            db.add(row)
            _supersede_current(db, user_id, row.domain, row.id)
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        psychosocial_trace.mark_failed(
            db,
            trace,
            StructuredAnalysisError("provider_error", error_code="result_persistence_failed"),
        )
        logger.error("Agent 4 result could not be persisted")
        return ExtractionOutcome(correlation_id, trace.id, trace.status)

    return ExtractionOutcome(
        correlation_id=correlation_id,
        trace_id=trace.id,
        status="succeeded",
        observation_ids=[row.id for row in stored],
        domains=[row.domain for row in stored],
        note=extraction.overall_note,
    )


# ----------------------------------------------- professional corrections ---
def fact_content_for(observation: PsychosocialObservation) -> str:
    domain: Domain | None = DOMAIN_BY_KEY.get(observation.domain)
    label = domain.label if domain else observation.domain
    quote = f" Cita del paciente: «{observation.evidence_quote}»." if observation.evidence_quote else ""
    return f"[Contexto psicosocial · {label}] {observation.summary}{quote}"


def confirm_observation(
    db: Session,
    observation: PsychosocialObservation,
    *,
    declared_by: str = "professional",
) -> ConfirmedFact:
    """Promote one inference to a confirmed fact, without deleting the inference.

    The observation keeps its lineage (which text, which Agent 4 call); what
    changes is that a human now stands behind it, so later extractions can no
    longer overwrite the domain on their own.
    """
    fact = ConfirmedFact(
        user_id=observation.user_id,
        category=PSYCHOSOCIAL_FACT_CATEGORY,
        content=fact_content_for(observation),
        declared_by=declared_by,
    )
    db.add(fact)
    db.flush()
    observation.confirmed_fact_id = fact.id
    observation.is_current = True
    observation.dismissed_at = None
    observation.dismissed_reason = None
    db.commit()
    db.refresh(fact)
    return fact


def dismiss_observation(
    db: Session,
    observation: PsychosocialObservation,
    *,
    reason: str,
) -> PsychosocialObservation:
    """Retire a wrong reading. The row stays for audit, out of the profile."""
    observation.is_current = False
    observation.dismissed_at = datetime.utcnow()
    observation.dismissed_reason = reason
    db.commit()
    db.refresh(observation)
    return observation


def record_professional_observation(
    db: Session,
    user_id,
    *,
    domain: str,
    state: str,
    direction: str = "desconocido",
    onset: str = "desconocido",
    summary: str,
    evidence_quote: str | None = None,
) -> PsychosocialObservation:
    """A professional records social context the texts never mentioned.

    Stored with ``recorded_by='professional'`` and full confidence: this is a
    declaration, so ``build_profile`` treats it as fact-grade and no Agent 4
    run can supersede it.
    """
    if domain not in DOMAIN_BY_KEY:
        raise ValueError(f"unknown psychosocial domain: {domain}")
    if state not in STATES:
        raise ValueError(f"unknown psychosocial state: {state}")
    if direction not in DIRECTIONS:
        raise ValueError(f"unknown psychosocial direction: {direction}")
    if onset not in ONSETS:
        raise ValueError(f"unknown psychosocial onset: {onset}")

    row = PsychosocialObservation(
        id=uuid.uuid4(),
        user_id=user_id,
        domain=domain,
        state=state,
        direction=direction,
        onset=onset,
        confidence=1.0,
        summary=summary,
        evidence_quote=evidence_quote,
        source_type="professional",
        source_id=None,
        recorded_by="professional",
        is_current=True,
        observed_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    _supersede_current(db, user_id, domain, row.id)
    db.commit()
    db.refresh(row)
    return row


def recent_change_window_start(now: datetime | None = None) -> datetime:
    return (now or datetime.utcnow()) - timedelta(days=RECENT_CHANGE_DAYS)
