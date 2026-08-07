"""
Patient <-> professional assignment lifecycle (doc 14): a professional
requests a link, the patient must explicitly accept it (which also grants
the `professional_sharing` consent, doc 1's "consentimiento granular,
segmentado por propósito"), and either side -- or a supervisor/admin --
can end it later.
"""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Consent, PatientProfessionalAssignment, User
from app.schemas import AssignmentOut, AssignmentRequestIn
from app.security import get_current_user, require_professional, require_roles
from app.services import audit

router = APIRouter(prefix="/api/v1/assignments", tags=["assignments"])


def _enrich(db: Session, assignment: PatientProfessionalAssignment) -> AssignmentOut:
    patient = db.get(User, assignment.patient_id)
    professional = db.get(User, assignment.professional_id)
    return AssignmentOut(
        id=assignment.id,
        patient_id=assignment.patient_id,
        professional_id=assignment.professional_id,
        status=assignment.status,
        requested_at=assignment.requested_at,
        updated_at=assignment.updated_at,
        patient_email=patient.email if patient else None,
        patient_display_name=patient.display_name if patient else None,
        professional_email=professional.email if professional else None,
        professional_display_name=professional.display_name if professional else None,
    )


def _enrich_many(db: Session, rows: list[PatientProfessionalAssignment]) -> list[AssignmentOut]:
    return [_enrich(db, a) for a in rows]


@router.post("/request", response_model=AssignmentOut, status_code=201)
def request_assignment(payload: AssignmentRequestIn, db: Session = Depends(get_db), professional: User = Depends(require_professional)):
    if professional.role == "admin_clinical":
        # Admin manages the roster; therapists/supervisors request clinical links.
        raise HTTPException(status_code=403, detail="admin_clinical manages assignments via overrides; therapists request access")

    patient = db.query(User).filter(User.email == payload.patient_email, User.role == "patient").first()
    if not patient:
        raise HTTPException(status_code=404, detail="No patient with that email")

    existing = (
        db.query(PatientProfessionalAssignment)
        .filter(
            PatientProfessionalAssignment.patient_id == patient.id,
            PatientProfessionalAssignment.professional_id == professional.id,
            PatientProfessionalAssignment.status.in_(["pending", "active", "paused"]),
        )
        .first()
    )
    if existing:
        return _enrich(db, existing)

    assignment = PatientProfessionalAssignment(patient_id=patient.id, professional_id=professional.id, status="pending")
    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    audit.log(db, actor_id=professional.id, actor_role=professional.role, action="assignment_requested",
              entity_type="assignment", entity_id=assignment.id, extra={"patient_id": str(patient.id)})
    return _enrich(db, assignment)


@router.get("/mine", response_model=list[AssignmentOut])
def my_assignments(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role == "patient":
        rows = (
            db.query(PatientProfessionalAssignment)
            .filter(PatientProfessionalAssignment.patient_id == user.id)
            .order_by(PatientProfessionalAssignment.requested_at.desc())
            .all()
        )
    elif user.role in ("supervisor", "admin_clinical"):
        rows = db.query(PatientProfessionalAssignment).order_by(PatientProfessionalAssignment.requested_at.desc()).all()
    else:
        rows = (
            db.query(PatientProfessionalAssignment)
            .filter(PatientProfessionalAssignment.professional_id == user.id)
            .order_by(PatientProfessionalAssignment.requested_at.desc())
            .all()
        )
    return _enrich_many(db, rows)


@router.get("/all", response_model=list[AssignmentOut])
def all_assignments(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("supervisor", "admin_clinical")),
    status: str | None = None,
):
    q = db.query(PatientProfessionalAssignment)
    if status:
        q = q.filter(PatientProfessionalAssignment.status == status)
    rows = q.order_by(PatientProfessionalAssignment.requested_at.desc()).all()
    return _enrich_many(db, rows)


def _get_owned_assignment(db: Session, assignment_id: uuid.UUID, user: User) -> PatientProfessionalAssignment:
    assignment = db.get(PatientProfessionalAssignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    if user.role == "patient" and assignment.patient_id != user.id:
        raise HTTPException(status_code=403, detail="Not your assignment")
    if user.role == "therapist" and assignment.professional_id != user.id:
        raise HTTPException(status_code=403, detail="Not your assignment")
    return assignment


@router.post("/{assignment_id}/accept", response_model=AssignmentOut)
def accept_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != "patient":
        raise HTTPException(status_code=403, detail="Only the patient can accept an assignment")
    assignment = _get_owned_assignment(db, assignment_id, user)
    if assignment.status not in ("pending", "paused"):
        raise HTTPException(status_code=400, detail=f"Cannot accept assignment in status '{assignment.status}'")
    assignment.status = "active"
    assignment.updated_at = datetime.utcnow()

    already_granted = (
        db.query(Consent)
        .filter(Consent.user_id == user.id, Consent.consent_type == "professional_sharing", Consent.revoked_at.is_(None), Consent.granted.is_(True))
        .first()
    )
    if not already_granted:
        db.add(Consent(user_id=user.id, consent_type="professional_sharing", granted=True))

    db.commit()
    db.refresh(assignment)
    audit.log(db, actor_id=user.id, actor_role=user.role, action="assignment_accepted", entity_type="assignment", entity_id=assignment.id)
    return _enrich(db, assignment)


@router.post("/{assignment_id}/reject", response_model=AssignmentOut)
def reject_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != "patient":
        raise HTTPException(status_code=403, detail="Only the patient can reject an assignment")
    assignment = _get_owned_assignment(db, assignment_id, user)
    assignment.status = "rejected"
    assignment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(assignment)
    audit.log(db, actor_id=user.id, actor_role=user.role, action="assignment_rejected", entity_type="assignment", entity_id=assignment.id)
    return _enrich(db, assignment)


@router.post("/{assignment_id}/pause", response_model=AssignmentOut)
def pause_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assignment = db.get(PatientProfessionalAssignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    allowed = (
        (user.role == "patient" and assignment.patient_id == user.id)
        or (user.role == "therapist" and assignment.professional_id == user.id)
        or user.role in ("supervisor", "admin_clinical")
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed to pause this assignment")
    if assignment.status != "active":
        raise HTTPException(status_code=400, detail="Only active assignments can be paused")
    assignment.status = "paused"
    assignment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(assignment)
    audit.log(db, actor_id=user.id, actor_role=user.role, action="assignment_paused", entity_type="assignment", entity_id=assignment.id)
    return _enrich(db, assignment)


@router.post("/{assignment_id}/resume", response_model=AssignmentOut)
def resume_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Patient re-activates a paused link (or therapist/supervisor for operational continuity)."""
    assignment = db.get(PatientProfessionalAssignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    allowed = (
        (user.role == "patient" and assignment.patient_id == user.id)
        or (user.role == "therapist" and assignment.professional_id == user.id)
        or user.role in ("supervisor", "admin_clinical")
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed to resume this assignment")
    if assignment.status != "paused":
        raise HTTPException(status_code=400, detail="Only paused assignments can be resumed")
    assignment.status = "active"
    assignment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(assignment)
    audit.log(db, actor_id=user.id, actor_role=user.role, action="assignment_resumed", entity_type="assignment", entity_id=assignment.id)
    return _enrich(db, assignment)


@router.post("/{assignment_id}/end", response_model=AssignmentOut)
def end_assignment(assignment_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    assignment = db.get(PatientProfessionalAssignment, assignment_id)
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    allowed = (
        (user.role == "patient" and assignment.patient_id == user.id)
        or (user.role == "therapist" and assignment.professional_id == user.id)
        or user.role in ("supervisor", "admin_clinical")
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed to end this assignment")
    assignment.status = "ended"
    assignment.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(assignment)
    audit.log(db, actor_id=user.id, actor_role=user.role, action="assignment_ended", entity_type="assignment", entity_id=assignment.id)
    return _enrich(db, assignment)
