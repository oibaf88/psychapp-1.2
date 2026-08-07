import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Notification, User
from app.security import get_current_user

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    title: str | None
    body: str
    alert_level: int | None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[NotificationOut])
def list_my_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user), limit: int = 30):
    if user.role == "patient":
        query = db.query(Notification).filter(Notification.user_id == user.id, Notification.recipient_type == "patient")
    else:
        query = db.query(Notification).filter(Notification.professional_id == user.id, Notification.recipient_type == "professional")
    return query.order_by(Notification.created_at.desc()).limit(limit).all()


@router.post("/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    notif = db.get(Notification, notification_id)
    if not notif:
        return {"ok": False}
    if notif.user_id != user.id and notif.professional_id != user.id:
        return {"ok": False}
    notif.status = "read"
    notif.read_at = datetime.utcnow()
    db.commit()
    return {"ok": True}
