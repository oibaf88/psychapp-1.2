from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import TimelineOut
from app.security import require_patient
from app.services.timeline import build_timeline

router = APIRouter(prefix="/api/v1/timeline", tags=["timeline"])


@router.get("", response_model=TimelineOut)
def get_my_timeline(window_days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db), user: User = Depends(require_patient)):
    return build_timeline(db, user.id, window_days)
