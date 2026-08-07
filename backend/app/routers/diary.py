from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import DiaryEntry, User
from app.schemas import DiaryIn, DiaryOut
from app.security import require_patient
from app.services import audit, conversation, risk_engine

router = APIRouter(prefix="/api/v1/diary", tags=["diary"])


class DiaryCreateResponse(BaseModel):
    entry: DiaryOut
    ui_mode: str  # normal | support | crisis -- same semantics as /chat


@router.post("", response_model=DiaryCreateResponse, status_code=201)
def create_entry(payload: DiaryIn, db: Session = Depends(get_db), user: User = Depends(require_patient)):
    entry = DiaryEntry(user_id=user.id, content=payload.content)
    db.add(entry)
    db.commit()
    db.refresh(entry)

    audit.log(db, actor_id=user.id, actor_role=user.role, action="diary_created", entity_type="diary_entry", entity_id=entry.id)

    conversation.analyze_text_and_store(db, user.id, payload.content)
    assessment = risk_engine.run_and_persist(db, user.id)

    if assessment.alert_level == 4:
        ui_mode = "crisis"
    elif assessment.alert_level == 3:
        ui_mode = "support"
    else:
        ui_mode = "normal"

    return DiaryCreateResponse(entry=DiaryOut.model_validate(entry), ui_mode=ui_mode)


@router.get("", response_model=list[DiaryOut])
def list_entries(db: Session = Depends(get_db), user: User = Depends(require_patient), limit: int = 30):
    return (
        db.query(DiaryEntry)
        .filter(DiaryEntry.user_id == user.id)
        .order_by(DiaryEntry.created_at.desc())
        .limit(limit)
        .all()
    )
