from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ChatMessage, User
from app.schemas import ChatIn, ChatMessageOut, ChatOut
from app.security import require_patient
from app.services import conversation

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


@router.post("", response_model=ChatOut)
def send_message(payload: ChatIn, db: Session = Depends(get_db), user: User = Depends(require_patient)):
    result = conversation.get_reply(db, user, payload.message)
    return ChatOut(**result)


@router.get("/history", response_model=list[ChatMessageOut])
def history(db: Session = Depends(get_db), user: User = Depends(require_patient), limit: int = 50):
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(messages))
