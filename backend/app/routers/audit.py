import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
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


class AuditLogPageOut(BaseModel):
    items: list[AuditLogOut]
    total: int
    limit: int
    offset: int


@router.get("/page", response_model=AuditLogPageOut)
def page_audit_log(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("supervisor", "admin_clinical")),
    limit: int = Query(50, ge=10, le=200),
    offset: int = Query(0, ge=0),
):
    """Read the complete history in bounded pages instead of truncating it."""
    query = db.query(AuditLog)
    total = query.count()
    items = (
        query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AuditLogPageOut(items=items, total=total, limit=limit, offset=offset)


@router.get("", response_model=list[AuditLogOut])
def list_audit_log(
    db: Session = Depends(get_db),
    _: User = Depends(require_roles("supervisor", "admin_clinical")),
    limit: int = Query(100, ge=1, le=200),
):
    """Compatibility endpoint for callers that only need the newest rows."""
    return db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
