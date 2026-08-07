import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog, User
from app.security import require_roles

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditLogOut(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    actor_role: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    extra: Any
    created_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=list[AuditLogOut])
def list_audit_log(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("supervisor", "admin_clinical")),
    limit: int = 100,
):
    return db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
