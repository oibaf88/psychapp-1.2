from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.content.safety_resources import CRISIS_RESOURCES, SAFE_GROUNDING_ALTERNATIVES
from app.database import get_db
from app.models import SafetyPlan, User
from app.schemas import SafetyPlanIn, SafetyPlanOut
from app.security import require_patient

router = APIRouter(prefix="/api/v1/safety-plan", tags=["safety"])


@router.get("", response_model=SafetyPlanOut)
def get_plan(db: Session = Depends(get_db), user: User = Depends(require_patient)):
    plan = db.query(SafetyPlan).filter(SafetyPlan.user_id == user.id).first()
    if not plan:
        plan = SafetyPlan(user_id=user.id)
        db.add(plan)
        db.commit()
        db.refresh(plan)
    return plan


@router.put("", response_model=SafetyPlanOut)
def update_plan(payload: SafetyPlanIn, db: Session = Depends(get_db), user: User = Depends(require_patient)):
    plan = db.query(SafetyPlan).filter(SafetyPlan.user_id == user.id).first()
    if not plan:
        plan = SafetyPlan(user_id=user.id)
        db.add(plan)
    for field, value in payload.model_dump().items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("/resources")
def get_resources():
    """Static, server-owned crisis resources -- see app/content/safety_resources.py.
    Always available, no auth required, so the crisis button works even
    if a session has expired."""
    return {"resources": CRISIS_RESOURCES, "safe_grounding_alternatives": SAFE_GROUNDING_ALTERNATIVES}
