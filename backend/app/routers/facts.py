import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ConfirmedFact, User
from app.schemas import FactIn, FactOut
from app.security import get_current_user, require_patient
from app.services import audit, risk_engine

router = APIRouter(prefix="/api/v1/facts", tags=["facts"])

# Facts a patient may self-declare through the UI. Clinically-loaded
# categories (ideation_active, planning, consumption_crisis) can also be
# self-declared -- the docs are explicit that a user's own declaration of
# active ideation must be able to reach the risk engine (doc 17/18:
# `existe_declaracion`) -- but every write is fully audited and always
# immediately re-evaluated by the deterministic risk engine, never by the
# LLM.
PATIENT_DECLARABLE_CATEGORIES = {
    "medication_taken",
    "relapse",
    "consumption_crisis",
    "ideation_active",
    "planning",
    "correction",
    "other",
}


@router.get("", response_model=list[FactOut])
def list_my_facts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return (
        db.query(ConfirmedFact)
        .filter(ConfirmedFact.user_id == user.id, ConfirmedFact.is_active == True)  # noqa: E712
        .order_by(ConfirmedFact.created_at.desc())
        .all()
    )


@router.post("", response_model=FactOut, status_code=201)
def declare_fact(payload: FactIn, db: Session = Depends(get_db), user: User = Depends(require_patient)):
    if payload.category not in PATIENT_DECLARABLE_CATEGORIES:
        raise HTTPException(status_code=400, detail=f"category must be one of {sorted(PATIENT_DECLARABLE_CATEGORIES)}")

    fact = ConfirmedFact(user_id=user.id, category=payload.category, content=payload.content, declared_by="user")
    db.add(fact)
    db.commit()
    db.refresh(fact)

    audit.log(db, actor_id=user.id, actor_role=user.role, action="fact_declared", entity_type="confirmed_fact", entity_id=fact.id,
              extra={"category": payload.category})

    # A new fact (especially a critical one) must immediately be able to
    # change the alert level -- never wait for the next check-in/chat turn.
    risk_engine.run_and_persist(db, user.id)

    return fact


@router.post("/{fact_id}/correct", response_model=FactOut, status_code=201)
def correct_fact(fact_id: uuid.UUID, payload: FactIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """
    A correction never overwrites the original row (doc 1: "las
    correcciones crean una nueva versión, sin eliminar la trazabilidad").
    It creates a new fact and marks the old one as superseded.
    """
    original = db.get(ConfirmedFact, fact_id)
    if not original or original.user_id != user.id:
        raise HTTPException(status_code=404, detail="Fact not found")

    declared_by = "user" if user.role == "patient" else "professional"
    new_fact = ConfirmedFact(
        user_id=original.user_id,
        category=payload.category,
        content=payload.content,
        declared_by=declared_by,
    )
    db.add(new_fact)
    db.flush()

    original.is_active = False
    original.superseded_by = new_fact.id
    db.commit()
    db.refresh(new_fact)

    audit.log(db, actor_id=user.id, actor_role=user.role, action="fact_corrected", entity_type="confirmed_fact",
              entity_id=new_fact.id, extra={"superseded": str(fact_id)})

    risk_engine.run_and_persist(db, original.user_id)
    return new_fact
