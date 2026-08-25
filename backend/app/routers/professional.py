"""
Professional panel API. Enforces the RBAC matrix from doc 16 as closely
as a single-tenant MVP reasonably can:

  therapist       -- only patients with an ACTIVE (or paused, read-only) assignment.
  supervisor      -- all patients (docs model "su equipo"; team scoping
                     was not implemented, so this is broader than spec).
  admin_clinical  -- manages assignments/professionals; per the matrix it
                     does NOT get clinical signal/fact visibility or
                     alert-management rights.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.content.safety_resources import (
    LEVEL3_PROFESSIONAL_NOTIFICATION_TEMPLATE,
    LEVEL4_PROFESSIONAL_NOTIFICATION_TEMPLATE,
)
from app.database import get_db
from app.models import (
    Agent2AnalysisTrace,
    AppUsageData,
    BiometricData,
    AlfaSignal,
    CheckIn,
    ConfirmedFact,
    ChatMessage,
    DiaryEntry,
    PatientProfessionalAssignment,
    ProfessionalAlert,
    PsychosocialObservation,
    RiskAssessment,
    SafetyPlan,
    User,
)
from app.schemas import (
    Agent2AnalysisTraceOut,
    AlertDismissIn,
    AlertOut,
    AlertResolveIn,
    CheckInOut,
    CopilotAskIn,
    CopilotMessageOut,
    CopilotSummaryIn,
    DiaryOut,
    EvidenceItemOut,
    FactIn,
    FactOut,
    LevelExplanationOut,
    PatientChatMessageOut,
    PatientDossierOut,
    PatientMetricsOut,
    PsychosocialAdjudicationIn,
    PatientProfileIn,
    PatientProfileOut,
    SignalRefutationIn,
    SignalRefutationOut,
    PsychosocialExplanationOut,
    PsychosocialObservationOut,
    DeepStatisticalAnalysisOut,
    BiometricDataOut,
    AppUsageDataOut,
    PatientSummaryOut,
    RiskAssessmentOut,
    SafetyPlanOut,
    SignalOut,
    StructuralExplanationOut,
    TimelineOut,
)
from app.security import require_professional
from app.services import agent2_trace, audit, clinical_copilot, clinical_view, psychosocial, risk_engine
from app.services import profile as profile_service
from app.services import signals as signals_service
from app.services.timeline import build_timeline

router = APIRouter(prefix="/api/v1/professional", tags=["professional"])


def _iso(value: datetime | None) -> str | None:
    """Serialize legacy naive database timestamps explicitly as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _agent2_trace_out(
    db: Session,
    trace: Agent2AnalysisTrace,
    *,
    source=None,
    signal: AlfaSignal | None = None,
    assessment: RiskAssessment | None = None,
    prefetched: bool = False,
) -> Agent2AnalysisTraceOut:
    source_id = trace.chat_message_id if trace.source_type == "chat_message" else trace.diary_entry_id
    if not prefetched:
        source_model = ChatMessage if trace.source_type == "chat_message" else DiaryEntry
        source = db.get(source_model, source_id)

    # Guard against corrupt/cross-patient links even though the database
    # constraints and write path already set them together.
    source_text = ""
    if source is not None and source.user_id == trace.user_id:
        source_text = source.content

    if not prefetched:
        signal = (
            db.query(AlfaSignal)
            .filter(AlfaSignal.user_id == trace.user_id, AlfaSignal.agent2_trace_id == trace.id)
            .order_by(AlfaSignal.timestamp.desc())
            .first()
        )
        assessment_query = db.query(RiskAssessment).filter(RiskAssessment.user_id == trace.user_id)
        if signal:
            assessment_query = assessment_query.filter(
                or_(
                    RiskAssessment.agent2_trace_id == trace.id,
                    RiskAssessment.linguistic_signal_id_used == signal.id,
                )
            )
        else:
            assessment_query = assessment_query.filter(RiskAssessment.agent2_trace_id == trace.id)
        assessment = assessment_query.order_by(RiskAssessment.calculated_at.desc()).first()

    return Agent2AnalysisTraceOut(
        id=trace.id,
        correlation_id=trace.correlation_id,
        source_type=trace.source_type,
        source_id=source_id,
        source_text=source_text,
        status=trace.status,
        provider=trace.provider,
        requested_model=trace.requested_model,
        response_model=trace.response_model,
        effort=trace.effort,
        max_tokens=trace.max_tokens,
        prompt_version=trace.prompt_version,
        prompt_sha256=trace.prompt_sha256,
        schema_version=trace.schema_version,
        schema_sha256=trace.schema_sha256,
        provider_message_id=trace.provider_message_id,
        provider_request_id=trace.provider_request_id,
        stop_reason=trace.stop_reason,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        cache_creation_input_tokens=trace.cache_creation_input_tokens,
        cache_read_input_tokens=trace.cache_read_input_tokens,
        latency_ms=trace.latency_ms,
        error_kind=trace.error_kind,
        error_code=trace.error_code,
        http_status=trace.http_status,
        app_release=trace.app_release,
        started_at=trace.started_at,
        completed_at=trace.completed_at,
        analysis=signal.value if signal else None,
        signal_id=signal.id if signal else None,
        risk_assessment_id=assessment.id if assessment else None,
        used_by_risk_engine=bool(
            signal and assessment and assessment.linguistic_signal_id_used == signal.id
        ),
    )


def _agent2_traces_out(db: Session, traces: list[Agent2AnalysisTrace]) -> list[Agent2AnalysisTraceOut]:
    """Hydrate a page of traces in four bounded queries instead of N+1."""
    if not traces:
        return []

    trace_ids = [trace.id for trace in traces]
    trace_user_by_id = {trace.id: trace.user_id for trace in traces}
    allowed_user_ids = {trace.user_id for trace in traces}
    chat_ids = [trace.chat_message_id for trace in traces if trace.chat_message_id]
    diary_ids = [trace.diary_entry_id for trace in traces if trace.diary_entry_id]
    sources: dict[uuid.UUID, object] = {}
    if chat_ids:
        sources.update({row.id: row for row in db.query(ChatMessage).filter(ChatMessage.id.in_(chat_ids)).all()})
    if diary_ids:
        sources.update({row.id: row for row in db.query(DiaryEntry).filter(DiaryEntry.id.in_(diary_ids)).all()})

    signals = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.agent2_trace_id.in_(trace_ids),
            AlfaSignal.user_id.in_(allowed_user_ids),
        )
        .order_by(AlfaSignal.timestamp.desc())
        .all()
    )
    signal_by_trace: dict[uuid.UUID, AlfaSignal] = {}
    trace_by_signal: dict[uuid.UUID, uuid.UUID] = {}
    for signal in signals:
        if trace_user_by_id.get(signal.agent2_trace_id) != signal.user_id:
            continue
        if signal.agent2_trace_id not in signal_by_trace:
            signal_by_trace[signal.agent2_trace_id] = signal
        trace_by_signal[signal.id] = signal.agent2_trace_id

    assessment_filters = [RiskAssessment.agent2_trace_id.in_(trace_ids)]
    if trace_by_signal:
        assessment_filters.append(RiskAssessment.linguistic_signal_id_used.in_(list(trace_by_signal)))
    assessments = (
        db.query(RiskAssessment)
        .filter(
            RiskAssessment.user_id.in_(allowed_user_ids),
            or_(*assessment_filters),
        )
        .order_by(RiskAssessment.calculated_at.desc())
        .all()
    )
    assessment_by_trace: dict[uuid.UUID, RiskAssessment] = {}
    trace_id_set = set(trace_ids)
    for assessment in assessments:
        related_trace_ids: set[uuid.UUID] = set()
        if (
            assessment.agent2_trace_id in trace_id_set
            and trace_user_by_id.get(assessment.agent2_trace_id) == assessment.user_id
        ):
            related_trace_ids.add(assessment.agent2_trace_id)
        signal_trace_id = trace_by_signal.get(assessment.linguistic_signal_id_used)
        if signal_trace_id and trace_user_by_id.get(signal_trace_id) == assessment.user_id:
            related_trace_ids.add(signal_trace_id)
        for trace_id in related_trace_ids:
            assessment_by_trace.setdefault(trace_id, assessment)

    return [
        _agent2_trace_out(
            db,
            trace,
            source=sources.get(
                trace.chat_message_id if trace.source_type == "chat_message" else trace.diary_entry_id
            ),
            signal=signal_by_trace.get(trace.id),
            assessment=assessment_by_trace.get(trace.id),
            prefetched=True,
        )
        for trace in traces
    ]


def _assignment(db: Session, patient_id, professional_id, statuses=("active",)) -> PatientProfessionalAssignment | None:
    return (
        db.query(PatientProfessionalAssignment)
        .filter(
            PatientProfessionalAssignment.patient_id == patient_id,
            PatientProfessionalAssignment.professional_id == professional_id,
            PatientProfessionalAssignment.status.in_(list(statuses)),
        )
        .first()
    )


def _active_assignment(db: Session, patient_id, professional_id) -> PatientProfessionalAssignment | None:
    return _assignment(db, patient_id, professional_id, statuses=("active",))


def _require_clinical_read(db: Session, professional: User, patient_id: uuid.UUID) -> None:
    """Therapist needs active/paused assignment; supervisor any patient; admin blocked."""
    if professional.role == "admin_clinical":
        raise HTTPException(
            status_code=403,
            detail="admin_clinical no tiene visibilidad clínica de señales/hechos (RBAC doc 16)",
        )
    if professional.role == "therapist":
        if not _assignment(db, patient_id, professional.id, statuses=("active", "paused")):
            raise HTTPException(status_code=403, detail="No tienes asignación activa/pausada con este paciente")
    # supervisor: full clinical read (assumption in README)


def _require_fact_access(professional: User, db: Session, patient_id):
    if professional.role != "therapist":
        raise HTTPException(status_code=403, detail="Solo el terapeuta asignado ve/edita hechos confirmados (RBAC)")
    if not _active_assignment(db, patient_id, professional.id):
        raise HTTPException(status_code=403, detail="Se requiere asignación activa para hechos")


def _alert_out(db: Session, alert: ProfessionalAlert, *, with_evidence: bool = True) -> AlertOut:
    """Serialize an alert together with *why* it fired and *what text* caused it.

    An alert that only carries a title and a rule code cannot be triaged: the
    therapist has to be able to read the sentence or the declaration behind
    it without leaving the row.
    """
    patient = db.get(User, alert.user_id)

    rule_code = None
    rule_title = None
    family = None
    family_label = None
    plain = None
    what_now = None
    evidence = None
    if with_evidence:
        assessment = (
            db.get(RiskAssessment, alert.related_assessment_id) if alert.related_assessment_id else None
        )
        if assessment is None or assessment.user_id != alert.user_id:
            assessment = (
                db.query(RiskAssessment)
                .filter(
                    RiskAssessment.user_id == alert.user_id,
                    RiskAssessment.generated_alert_id == alert.id,
                )
                .order_by(RiskAssessment.calculated_at.desc())
                .first()
            )
        if assessment is not None:
            rule_code = clinical_view.selected_rule_code(assessment)
            info = clinical_view.rule_info(rule_code)
            rule_title = info["title"]
            family = info["family"]
            family_label = clinical_view.FAMILY_LABELS.get(family, family)
            plain = info["plain"]
            what_now = info["what_now"]
            evidence = clinical_view.evidence_for_assessment(db, assessment)

    return AlertOut(
        id=alert.id,
        user_id=alert.user_id,
        alert_level=alert.alert_level,
        status=alert.status,
        title=alert.title,
        description=alert.description,
        related_signals=alert.related_signals,
        created_at=alert.created_at,
        acknowledged_at=alert.acknowledged_at,
        resolved_at=alert.resolved_at,
        resolution_notes=getattr(alert, "resolution_notes", None),
        dismiss_reason=getattr(alert, "dismiss_reason", None),
        patient_display_name=patient.display_name if patient else None,
        patient_email=patient.email if patient else None,
        related_assessment_id=alert.related_assessment_id,
        rule_code=rule_code,
        rule_title=rule_title,
        driver_family=family,
        driver_family_label=family_label,
        plain_explanation=plain,
        what_now=what_now,
        evidence=evidence,
    )


def _latest_assessment(db: Session, patient_id) -> RiskAssessment | None:
    return (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient_id)
        .order_by(RiskAssessment.calculated_at.desc())
        .first()
    )


def _patient_summary(db: Session, patient: User, status_label: str) -> PatientSummaryOut:
    assessment = _latest_assessment(db, patient.id)
    latest_score = None
    latest_band = None
    if assessment and isinstance(assessment.input_signals, dict):
        latest_score = assessment.input_signals.get("structural_score")
        latest_band = assessment.input_signals.get("confidence_band")

    open_alerts = (
        db.query(ProfessionalAlert)
        .filter(
            ProfessionalAlert.user_id == patient.id,
            ProfessionalAlert.status.in_(["open", "acknowledged"]),
            ProfessionalAlert.source == "rule_engine",
        )
        .count()
    )
    checkin_count = db.query(CheckIn).filter(CheckIn.user_id == patient.id).count()
    last_ci = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == patient.id)
        .order_by(CheckIn.created_at.desc())
        .first()
    )
    return PatientSummaryOut(
        id=patient.id,
        display_name=patient.display_name,
        email=patient.email,
        assignment_status=status_label,
        latest_alert_level=assessment.alert_level if assessment else None,
        latest_structural_score=latest_score,
        latest_confidence_band=latest_band,
        open_alerts=open_alerts,
        checkin_count=checkin_count,
        last_checkin_at=last_ci.created_at if last_ci else None,
    )


@router.get("/patients", response_model=list[PatientSummaryOut])
def list_patients(db: Session = Depends(get_db), professional: User = Depends(require_professional)):
    if professional.role == "therapist":
        assignments = (
            db.query(PatientProfessionalAssignment)
            .filter(
                PatientProfessionalAssignment.professional_id == professional.id,
                PatientProfessionalAssignment.status.in_(["pending", "active", "paused"]),
            )
            .all()
        )
        patients = [(db.get(User, a.patient_id), a.status) for a in assignments]
    else:
        patients = [(u, "roster") for u in db.query(User).filter(User.role == "patient").all()]

    summaries: list[PatientSummaryOut] = []
    for patient, status_label in patients:
        if patient is None:
            continue
        if professional.role == "admin_clinical":
            summaries.append(
                PatientSummaryOut(
                    id=patient.id,
                    display_name=patient.display_name,
                    email=patient.email,
                    assignment_status=status_label,
                    latest_alert_level=None,
                    open_alerts=0,
                    checkin_count=0,
                )
            )
            continue
        summaries.append(_patient_summary(db, patient, status_label))
    return summaries


@router.get("/patients/{patient_id}/timeline", response_model=TimelineOut)
def patient_timeline(
    patient_id: uuid.UUID,
    window_days: int = 30,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_clinical_read(db, professional, patient_id)
    return build_timeline(db, patient_id, window_days)


@router.get("/patients/{patient_id}/facts", response_model=list[FactOut])
def patient_facts(
    patient_id: uuid.UUID,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_fact_access(professional, db, patient_id)
    return (
        db.query(ConfirmedFact)
        .filter(ConfirmedFact.user_id == patient_id, ConfirmedFact.is_active == True)  # noqa: E712
        .order_by(ConfirmedFact.created_at.desc())
        .all()
    )


@router.post("/patients/{patient_id}/facts", response_model=FactOut, status_code=201)
def declare_fact_for_patient(
    patient_id: uuid.UUID,
    payload: FactIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Therapist may register confirmed facts for an actively assigned patient."""
    _require_fact_access(professional, db, patient_id)
    allowed = {
        "medication_taken",
        "relapse",
        "consumption_crisis",
        "ideation_active",
        "planning",
        "correction",
        "other",
    }
    if payload.category not in allowed:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(allowed)}")

    fact = ConfirmedFact(
        user_id=patient_id,
        category=payload.category,
        content=payload.content,
        declared_by="professional",
    )
    db.add(fact)
    db.commit()
    db.refresh(fact)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="fact_declared_by_professional",
        entity_type="confirmed_fact",
        entity_id=fact.id,
        extra={"patient_id": str(patient_id), "category": payload.category},
    )
    risk_engine.run_and_persist(db, patient_id)
    return fact


@router.get("/patients/{patient_id}/checkins", response_model=list[CheckInOut])
def patient_checkins(
    patient_id: uuid.UUID,
    limit: int = 60,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_clinical_read(db, professional, patient_id)
    return (
        db.query(CheckIn)
        .filter(CheckIn.user_id == patient_id)
        .order_by(CheckIn.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )


@router.get("/patients/{patient_id}/diary", response_model=list[DiaryOut])
def patient_diary(
    patient_id: uuid.UUID,
    limit: int = 40,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_clinical_read(db, professional, patient_id)
    return (
        db.query(DiaryEntry)
        .filter(DiaryEntry.user_id == patient_id)
        .order_by(DiaryEntry.created_at.desc())
        .limit(min(limit, 100))
        .all()
    )


@router.get("/patients/{patient_id}/assessments", response_model=list[RiskAssessmentOut])
def patient_assessments(
    patient_id: uuid.UUID,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_clinical_read(db, professional, patient_id)
    return (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient_id)
        .order_by(RiskAssessment.calculated_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


@router.get("/patients/{patient_id}/signals", response_model=list[SignalOut])
def patient_signals(
    patient_id: uuid.UUID,
    limit: int = 40,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_clinical_read(db, professional, patient_id)
    return (
        db.query(AlfaSignal)
        .filter(AlfaSignal.user_id == patient_id)
        .order_by(AlfaSignal.timestamp.desc())
        .limit(min(limit, 100))
        .all()
    )


@router.get("/patients/{patient_id}/profile", response_model=PatientProfileOut)
def patient_profile(
    patient_id: uuid.UUID,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """What the system has accumulated about this person, and what it plans
    to bring up next.

    Read-only for most of the panel, but the point of showing it is that a
    clinician can disagree with it: a portrait is a model's summary of a
    patient, and one nobody can correct is one nobody should trust.
    """
    _require_clinical_read(db, professional, patient_id)
    return PatientProfileOut(**profile_service.as_dict(profile_service.get(db, patient_id)))


@router.put("/patients/{patient_id}/profile", response_model=PatientProfileOut)
def update_patient_profile(
    patient_id: uuid.UUID,
    payload: PatientProfileIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Correct the portrait, or set what to explore next.

    Requires the same access as adjudicating an observation: this shapes
    what the conversational agent brings up with the patient, so it belongs
    to the assigned therapist rather than to anyone with read access.
    """
    _require_fact_access(professional, db, patient_id)
    changed = []
    if payload.portrait is not None:
        profile_service.set_portrait_by_clinician(
            db, patient_id, portrait=payload.portrait, actor_id=professional.id
        )
        changed.append("portrait")
    if payload.open_threads is not None:
        profile_service.set_open_threads(
            db, patient_id, [t.model_dump() for t in payload.open_threads]
        )
        changed.append("open_threads")

    if changed:
        audit.log(
            db,
            actor_id=professional.id,
            actor_role=professional.role,
            action="patient_profile_edited",
            entity_type="patient_profile",
            entity_id=patient_id,
            extra={"patient_id": str(patient_id), "changed": changed},
        )
    # No re-evaluation: the profile shapes conversation and comparison, and
    # nothing here is an input the deterministic engine reads as evidence.
    return PatientProfileOut(**profile_service.as_dict(profile_service.get(db, patient_id)))


@router.post(
    "/patients/{patient_id}/signals/{signal_id}/refute",
    response_model=SignalRefutationOut,
)
def refute_linguistic_signal(
    patient_id: uuid.UUID,
    signal_id: uuid.UUID,
    payload: SignalRefutationIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Mark a linguistic inference as wrong, and re-evaluate immediately.

    Psychosocial observations have had this since they existed; linguistic
    signals never did. A wrong flag kept firing its rule on every evaluation
    for the whole freshness window with no way to say so — which is how an
    emergency alert for someone announcing a decision to change their life
    stayed on the record.

    The signal row is kept and deactivated, never deleted: the trace, the
    source text and the alert it produced are the reason a clinician can
    review the decision at all. The reason becomes a `correction` fact, and
    the engine picks the change up on the next evaluation without any rule
    changing, because every query it makes already filters `is_active`.
    """
    _require_fact_access(professional, db, patient_id)
    signal = db.get(AlfaSignal, signal_id)
    if signal is None or signal.user_id != patient_id:
        raise HTTPException(status_code=404, detail="Señal no encontrada")
    if not signal.is_active:
        raise HTTPException(status_code=409, detail="Esta señal ya estaba refutada")

    before = _latest_assessment(db, patient_id)
    level_before = before.alert_level if before else 0

    try:
        result = signals_service.refute(
            db,
            signal,
            actor_id=professional.id,
            actor_role=professional.role,
            reason=payload.reason,
        )
    except signals_service.SignalNotRefutable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="linguistic_signal_refuted",
        entity_type="alfa_signal",
        entity_id=signal.id,
        extra={
            "patient_id": str(patient_id),
            "signal_type": signal.signal_type,
            "correction_fact_id": str(result.fact.id),
        },
    )

    # Immediate re-evaluation, for the same reason adjudication re-runs it:
    # leaving the panel showing a level the system no longer stands behind
    # is worse than the wrong level was.
    assessment = risk_engine.run_and_persist(db, patient_id)
    return SignalRefutationOut(
        signal_id=signal.id,
        is_active=signal.is_active,
        superseded_by_fact=signal.superseded_by_fact,
        correction_fact_id=result.fact.id,
        alert_level_before=level_before,
        alert_level_after=assessment.alert_level,
    )


@router.get("/patients/{patient_id}/agent2-analyses", response_model=list[Agent2AnalysisTraceOut])
def patient_agent2_analyses(
    patient_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Text input, validated Agent 2 output and call lineage for clinical review."""
    _require_clinical_read(db, professional, patient_id)
    rows = (
        db.query(Agent2AnalysisTrace)
        .filter(
            Agent2AnalysisTrace.user_id == patient_id,
            Agent2AnalysisTrace.agent_role.in_(agent2_trace.LINGUISTIC_ROLES),
        )
        .order_by(Agent2AnalysisTrace.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    result = _agent2_traces_out(db, rows)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="agent2_analysis_history_viewed",
        entity_type="user",
        entity_id=patient_id,
        extra={"row_count": len(rows), "offset": offset, "limit": limit},
    )
    return result


@router.get("/patients/{patient_id}/safety-plan", response_model=SafetyPlanOut | None)
def patient_safety_plan(
    patient_id: uuid.UUID,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_clinical_read(db, professional, patient_id)
    return db.query(SafetyPlan).filter(SafetyPlan.user_id == patient_id).first()


@router.get("/patients/{patient_id}/dossier", response_model=PatientDossierOut)
def patient_dossier(
    patient_id: uuid.UUID,
    window_days: int = 30,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """
    Historial clínico completo del paciente para el profesional asignado.
    No depende de que exista una alerta: es el acceso rutinario de seguimiento.
    """
    _require_clinical_read(db, professional, patient_id)
    patient = db.get(User, patient_id)
    if not patient or patient.role != "patient":
        raise HTTPException(status_code=404, detail="Paciente no encontrado")

    status_label = "roster"
    if professional.role == "therapist":
        a = _assignment(db, patient_id, professional.id, statuses=("pending", "active", "paused", "ended"))
        status_label = a.status if a else "none"

    assessment = _latest_assessment(db, patient_id)
    timeline = build_timeline(db, patient_id, window_days)
    checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == patient_id)
        .order_by(CheckIn.created_at.desc())
        .limit(60)
        .all()
    )
    diary = (
        db.query(DiaryEntry)
        .filter(DiaryEntry.user_id == patient_id)
        .order_by(DiaryEntry.created_at.desc())
        .limit(40)
        .all()
    )
    # Facts: therapist only; supervisor sees empty + note via empty list
    facts: list = []
    if professional.role == "therapist" and _active_assignment(db, patient_id, professional.id):
        facts = (
            db.query(ConfirmedFact)
            .filter(ConfirmedFact.user_id == patient_id, ConfirmedFact.is_active == True)  # noqa: E712
            .order_by(ConfirmedFact.created_at.desc())
            .all()
        )

    assessments = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient_id)
        .order_by(RiskAssessment.calculated_at.desc())
        .limit(30)
        .all()
    )
    alerts = (
        db.query(ProfessionalAlert)
        .filter(ProfessionalAlert.user_id == patient_id)
        .order_by(ProfessionalAlert.created_at.desc())
        .limit(30)
        .all()
    )
    signals = (
        db.query(AlfaSignal)
        .filter(AlfaSignal.user_id == patient_id)
        .order_by(AlfaSignal.timestamp.desc())
        .limit(40)
        .all()
    )
    agent2_traces = (
        db.query(Agent2AnalysisTrace)
        .filter(
            Agent2AnalysisTrace.user_id == patient_id,
            Agent2AnalysisTrace.agent_role.in_(agent2_trace.LINGUISTIC_ROLES),
        )
        .order_by(Agent2AnalysisTrace.started_at.desc())
        .limit(50)
        .all()
    )

    biometrics = (
        db.query(BiometricData)
        .filter(BiometricData.user_id == patient_id)
        .order_by(BiometricData.measured_at.desc())
        .limit(30)
        .all()
    )

    app_usage = (
        db.query(AppUsageData)
        .filter(AppUsageData.user_id == patient_id)
        .order_by(AppUsageData.measured_at.desc())
        .limit(30)
        .all()
    )

    deep_analysis = DeepStatisticalAnalysisOut(
        biometrics=biometrics,
        app_usage=app_usage,
        insights=["Patrón de sueño irregular detectado en los últimos 3 días", "Uso excesivo de redes sociales a altas horas de la noche"]
    ) if (biometrics or app_usage) else None

    chat_messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == patient_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(120)
        .all()
    )
    chat_messages.reverse()

    plan = db.query(SafetyPlan).filter(SafetyPlan.user_id == patient_id).first()

    patient_label = patient.display_name
    protocol = {
        "level3": LEVEL3_PROFESSIONAL_NOTIFICATION_TEMPLATE.format(patient_label=patient_label),
        "level4": LEVEL4_PROFESSIONAL_NOTIFICATION_TEMPLATE.format(patient_label=patient_label),
        "notes": (
            "Nivel 0–1: autogestión. Nivel 2: prevención (sin alerta automática). "
            "Nivel 3: revisión profesional cuando sea posible. "
            "Nivel 4: atención inmediata; el paciente ve redirección a 024/112."
        ),
    }

    result = PatientDossierOut(
        patient=_patient_summary(db, patient, status_label),
        current_risk=RiskAssessmentOut.model_validate(assessment) if assessment else None,
        level_explanation=LevelExplanationOut(
            **clinical_view.level_explanation(
                assessment,
                driver_evidence=clinical_view.evidence_for_assessment(db, assessment),
            )
        ),
        structural_explanation=StructuralExplanationOut(**clinical_view.structural_explanation(assessment)),
        psychosocial_explanation=PsychosocialExplanationOut(
            **clinical_view.psychosocial_explanation(None, psychosocial.assess(db, patient_id))
        ),
        metrics=PatientMetricsOut(**clinical_view.build_metrics(db, patient_id, max(window_days, 90))),
        evidence=[EvidenceItemOut(**item) for item in clinical_view.build_evidence_feed(db, patient_id)],
        timeline=TimelineOut(**timeline),
        checkins=checkins,
        diary=diary,
        chat_messages=[PatientChatMessageOut.model_validate(row) for row in chat_messages],
        facts=facts,
        assessments=assessments,
        alerts=[_alert_out(db, a) for a in alerts],
        signals=signals,
        agent2_traces=_agent2_traces_out(db, agent2_traces),
        safety_plan=SafetyPlanOut.model_validate(plan) if plan else None,
        deep_analysis=deep_analysis,
        professional_protocol=protocol,
    )
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="patient_dossier_viewed",
        entity_type="user",
        entity_id=patient_id,
        extra={
            "assessment_count": len(assessments),
            "agent2_trace_count": len(agent2_traces),
            "chat_message_count": len(chat_messages),
            "window_days": window_days,
        },
    )
    return result


@router.get("/patients/{patient_id}/chat", response_model=list[PatientChatMessageOut])
def patient_chat(
    patient_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """The patient's own conversation with Agent 1, oldest first.

    Chat is a first-class clinical source: Agent 2 analyses it exactly like
    the diary, and a level-4 alert can be raised from a single chat turn.
    Reading it is therefore part of auditing an alert, not an extra.
    """
    _require_clinical_read(db, professional, patient_id)
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == patient_id)
        .order_by(ChatMessage.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    rows.reverse()
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="patient_chat_viewed",
        entity_type="user",
        entity_id=patient_id,
        extra={"row_count": len(rows), "offset": offset, "limit": limit},
    )
    return rows


@router.get("/patients/{patient_id}/metrics", response_model=PatientMetricsOut)
def patient_metrics(
    patient_id: uuid.UUID,
    window_days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Chart-ready series: check-ins, structural score, z-scores, Agent 2
    signals, alert-level history and event markers."""
    _require_clinical_read(db, professional, patient_id)
    return clinical_view.build_metrics(db, patient_id, window_days)


@router.get("/patients/{patient_id}/evidence", response_model=list[EvidenceItemOut])
def patient_evidence(
    patient_id: uuid.UUID,
    limit: int = Query(60, ge=1, le=200),
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Every analysed text with what the model read in it and what the
    deterministic engine did next."""
    _require_clinical_read(db, professional, patient_id)
    rows = clinical_view.build_evidence_feed(db, patient_id, limit)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="patient_evidence_viewed",
        entity_type="user",
        entity_id=patient_id,
        extra={"row_count": len(rows)},
    )
    return rows


def _psychosocial_observation_out(row: PsychosocialObservation) -> PsychosocialObservationOut:
    return PsychosocialObservationOut(
        id=row.id,
        domain=row.domain,
        domain_label=psychosocial.DOMAIN_LABELS.get(row.domain, row.domain),
        category=row.category,
        category_label=psychosocial.CATEGORY_LABELS.get(row.category, row.category),
        valence=row.valence,
        intensity=row.intensity,
        confidence=row.confidence,
        is_change=row.is_change,
        status=row.status,
        summary=row.summary,
        evidence_quote=row.evidence_quote,
        source_type=row.source_type,
        source_label="Chat" if row.source_type == "chat_message" else "Diario",
        source_id=row.chat_message_id or row.diary_entry_id,
        adjudication_note=row.adjudication_note,
        adjudicated_at=_iso(row.adjudicated_at),
        observed_at=_iso(row.observed_at),
    )


@router.get("/patients/{patient_id}/psychosocial", response_model=PsychosocialExplanationOut)
def patient_psychosocial(
    patient_id: uuid.UUID,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """The patient's social context: index, per-domain breakdown and quotes."""
    _require_clinical_read(db, professional, patient_id)
    live = psychosocial.assess(db, patient_id)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="patient_psychosocial_viewed",
        entity_type="user",
        entity_id=patient_id,
        extra={"active_domains": live.active_count, "index": live.index},
    )
    return PsychosocialExplanationOut(**clinical_view.psychosocial_explanation(None, live))


@router.get(
    "/patients/{patient_id}/psychosocial/observations",
    response_model=list[PsychosocialObservationOut],
)
def patient_psychosocial_observations(
    patient_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=300),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Full append-only history of extracted social determinants."""
    _require_clinical_read(db, professional, patient_id)
    rows = (
        db.query(PsychosocialObservation)
        .filter(PsychosocialObservation.user_id == patient_id)
        .order_by(PsychosocialObservation.observed_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_psychosocial_observation_out(row) for row in rows]


@router.post(
    "/patients/{patient_id}/psychosocial/observations/{observation_id}",
    response_model=PsychosocialObservationOut,
)
def adjudicate_psychosocial_observation(
    patient_id: uuid.UUID,
    observation_id: uuid.UUID,
    payload: PsychosocialAdjudicationIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Confirm or refute an extracted observation.

    This is the human half of the fact/inference wall for social context: a
    confirmed observation counts at full weight in the deterministic index
    regardless of what the model's own confidence was, and a refuted one stops
    counting entirely. Only the assigned therapist may adjudicate, matching
    the rule already used for confirmed facts.
    """
    _require_fact_access(professional, db, patient_id)
    row = db.get(PsychosocialObservation, observation_id)
    if row is None or row.user_id != patient_id:
        raise HTTPException(status_code=404, detail="Observación no encontrada")

    previous = row.status
    psychosocial.adjudicate(
        db, row, status=payload.status, actor_id=professional.id, note=payload.note
    )
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="psychosocial_observation_adjudicated",
        entity_type="psychosocial_observation",
        entity_id=row.id,
        extra={
            "patient_id": str(patient_id),
            "from": previous,
            "to": payload.status,
            "domain": row.domain,
            "category": row.category,
        },
    )
    # Adjudication changes the index, which can change the level. Re-run the
    # deterministic engine so the panel is not left showing a stale decision.
    risk_engine.run_and_persist(db, patient_id)
    return _psychosocial_observation_out(row)


@router.get("/patients/{patient_id}/explanation", response_model=LevelExplanationOut)
def patient_level_explanation(
    patient_id: uuid.UUID,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Plain-Spanish answer to 'why is this patient at this level right now'."""
    _require_clinical_read(db, professional, patient_id)
    assessment = _latest_assessment(db, patient_id)
    return LevelExplanationOut(
        **clinical_view.level_explanation(
            assessment,
            driver_evidence=clinical_view.evidence_for_assessment(db, assessment),
        )
    )


# --------------------------------------------------- Agent 3 · copilot -----
def _require_copilot_access(db: Session, professional: User, patient_id: uuid.UUID) -> User:
    """Same clinical-read rule as the dossier, plus the patient must exist.

    admin_clinical is rejected by ``_require_clinical_read`` and therefore can
    never open a conversation about a patient's clinical content.
    """
    _require_clinical_read(db, professional, patient_id)
    patient = db.get(User, patient_id)
    if not patient or patient.role != "patient":
        raise HTTPException(status_code=404, detail="Paciente no encontrado")
    return patient


@router.get("/patients/{patient_id}/copilot/messages", response_model=list[CopilotMessageOut])
def copilot_messages(
    patient_id: uuid.UUID,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """This professional's own copilot thread about this patient."""
    _require_copilot_access(db, professional, patient_id)
    return clinical_copilot.history(db, professional.id, patient_id)


@router.post("/patients/{patient_id}/copilot/messages", response_model=CopilotMessageOut, status_code=201)
def copilot_ask(
    patient_id: uuid.UUID,
    payload: CopilotAskIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Ask Agent 3 about this patient.

    Agent 3 is read-only over the clinical record: it cannot create facts,
    signals, assessments or alerts, so nothing it says can change the
    patient's alert level or what the patient sees.
    """
    patient = _require_copilot_access(db, professional, patient_id)
    answer = clinical_copilot.ask(
        db,
        professional=professional,
        patient=patient,
        question=payload.message,
        window_days=payload.window_days,
    )
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="copilot_question_asked",
        entity_type="user",
        entity_id=patient_id,
        extra={"window_days": payload.window_days, "error_kind": answer.error_kind},
    )
    return answer


@router.post("/patients/{patient_id}/copilot/summary", response_model=CopilotMessageOut, status_code=201)
def copilot_summary(
    patient_id: uuid.UUID,
    payload: CopilotSummaryIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Generate a fresh situation summary from what the patient has said."""
    patient = _require_copilot_access(db, professional, patient_id)
    answer = clinical_copilot.summarize(
        db,
        professional=professional,
        patient=patient,
        window_days=payload.window_days,
    )
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="copilot_summary_generated",
        entity_type="user",
        entity_id=patient_id,
        extra={"window_days": payload.window_days, "error_kind": answer.error_kind},
    )
    return answer


@router.post("/patients/{patient_id}/reevaluate", response_model=RiskAssessmentOut)
def reevaluate_patient(
    patient_id: uuid.UUID,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    """Force a deterministic risk re-run (therapist/supervisor)."""
    _require_clinical_read(db, professional, patient_id)
    if professional.role not in ("therapist", "supervisor"):
        raise HTTPException(status_code=403, detail="Solo terapeuta o supervisor pueden reevaluar")
    assessment = risk_engine.run_and_persist(db, patient_id)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="risk_reevaluated",
        entity_type="risk_assessment",
        entity_id=assessment.id,
        extra={"patient_id": str(patient_id), "level": assessment.alert_level},
    )
    return assessment


# ------------------------------------------------------------- alerts -----
@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    status_filter: str | None = Query(None, alias="status"),
    alert_level: int | None = None,
    user_id: uuid.UUID | None = None,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    if professional.role == "admin_clinical":
        raise HTTPException(
            status_code=403,
            detail="admin_clinical no tiene gestión de alertas clínicas (RBAC doc 16).",
        )

    query = db.query(ProfessionalAlert).filter(ProfessionalAlert.source == "rule_engine")

    if professional.role == "therapist":
        assigned_ids = [
            a.patient_id
            for a in db.query(PatientProfessionalAssignment).filter(
                PatientProfessionalAssignment.professional_id == professional.id,
                PatientProfessionalAssignment.status.in_(["active", "paused"]),
            )
        ]
        query = query.filter(ProfessionalAlert.user_id.in_(assigned_ids or [uuid.uuid4()]))

    if status_filter:
        query = query.filter(ProfessionalAlert.status == status_filter)
    if alert_level:
        query = query.filter(ProfessionalAlert.alert_level == alert_level)
    if user_id:
        query = query.filter(ProfessionalAlert.user_id == user_id)

    rows = query.order_by(ProfessionalAlert.created_at.desc()).all()
    return [_alert_out(db, a) for a in rows]


def _require_alert_management(professional: User):
    if professional.role == "admin_clinical":
        raise HTTPException(status_code=403, detail="admin_clinical cannot manage alerts (see RBAC matrix, doc 16)")


def _get_alert_for_professional(db: Session, alert_id: uuid.UUID, professional: User) -> ProfessionalAlert:
    alert = db.get(ProfessionalAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if professional.role == "therapist" and not _assignment(
        db, alert.user_id, professional.id, statuses=("active", "paused")
    ):
        raise HTTPException(status_code=403, detail="Not one of your patients")
    return alert


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(alert_id: uuid.UUID, db: Session = Depends(get_db), professional: User = Depends(require_professional)):
    _require_alert_management(professional)
    alert = _get_alert_for_professional(db, alert_id, professional)
    alert.status = "acknowledged"
    alert.acknowledged_at = datetime.utcnow()
    db.commit()
    db.refresh(alert)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="alert_acknowledged",
        entity_type="professional_alert",
        entity_id=alert.id,
    )
    return _alert_out(db, alert)


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: uuid.UUID,
    payload: AlertResolveIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_alert_management(professional)
    alert = _get_alert_for_professional(db, alert_id, professional)
    alert.status = "resolved"
    alert.resolved_at = datetime.utcnow()
    alert.resolution_notes = payload.resolution_notes
    db.commit()
    db.refresh(alert)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="alert_resolved",
        entity_type="professional_alert",
        entity_id=alert.id,
        extra={"notes": payload.resolution_notes},
    )
    return _alert_out(db, alert)


@router.post("/alerts/{alert_id}/dismiss", response_model=AlertOut)
def dismiss_alert(
    alert_id: uuid.UUID,
    payload: AlertDismissIn,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_alert_management(professional)
    alert = _get_alert_for_professional(db, alert_id, professional)
    if alert.alert_level == 4 and not payload.dismiss_reason.strip():
        raise HTTPException(status_code=400, detail="Level 4 alerts require a justification to dismiss (doc 16)")
    alert.status = "dismissed"
    alert.dismiss_reason = payload.dismiss_reason
    db.commit()
    db.refresh(alert)
    audit.log(
        db,
        actor_id=professional.id,
        actor_role=professional.role,
        action="alert_dismissed",
        entity_type="professional_alert",
        entity_id=alert.id,
        extra={"reason": payload.dismiss_reason},
    )
    return _alert_out(db, alert)
