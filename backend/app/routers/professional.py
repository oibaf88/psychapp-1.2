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
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.content.safety_resources import (
    LEVEL3_PROFESSIONAL_NOTIFICATION_TEMPLATE,
    LEVEL4_PROFESSIONAL_NOTIFICATION_TEMPLATE,
)
from app.database import get_db
from app.models import (
    AlfaSignal,
    CheckIn,
    ConfirmedFact,
    DiaryEntry,
    PatientProfessionalAssignment,
    ProfessionalAlert,
    RiskAssessment,
    SafetyPlan,
    User,
)
from app.schemas import (
    AlertDismissIn,
    AlertOut,
    AlertResolveIn,
    CheckInOut,
    DiaryOut,
    FactIn,
    FactOut,
    PatientDossierOut,
    PatientSummaryOut,
    RiskAssessmentOut,
    SafetyPlanOut,
    SignalOut,
    TimelineOut,
)
from app.security import require_professional
from app.services import audit, risk_engine
from app.services.timeline import build_timeline

router = APIRouter(prefix="/api/v1/professional", tags=["professional"])


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


def _alert_out(db: Session, alert: ProfessionalAlert) -> AlertOut:
    patient = db.get(User, alert.user_id)
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
    limit: int = 30,
    db: Session = Depends(get_db),
    professional: User = Depends(require_professional),
):
    _require_clinical_read(db, professional, patient_id)
    return (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient_id)
        .order_by(RiskAssessment.calculated_at.desc())
        .limit(min(limit, 100))
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

    return PatientDossierOut(
        patient=_patient_summary(db, patient, status_label),
        current_risk=RiskAssessmentOut.model_validate(assessment) if assessment else None,
        timeline=TimelineOut(**timeline),
        checkins=checkins,
        diary=diary,
        facts=facts,
        assessments=assessments,
        alerts=[_alert_out(db, a) for a in alerts],
        signals=signals,
        safety_plan=SafetyPlanOut.model_validate(plan) if plan else None,
        professional_protocol=protocol,
    )


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
