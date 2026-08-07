from sqlalchemy.orm import Session

from app.models import AuditLog


def log(db: Session, *, actor_id=None, actor_role=None, action: str, entity_type: str | None = None,
        entity_id: str | None = None, extra: dict | None = None) -> None:
    entry = AuditLog(
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        extra=extra,
    )
    db.add(entry)
    db.commit()
