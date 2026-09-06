"""Administrative provisioning and role management for application users.

Public signup deliberately remains patient-only.  Privileged roles are created
or assigned through these endpoints, which are accessible only to an existing
``admin_clinical`` account.
"""
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Consent, SafetyPlan, User
from app.security import hash_password, require_admin
from app.services import audit

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])

UserRoleValue = Literal["patient", "therapist", "supervisor", "admin_clinical"]
ProfessionalRoleValue = Literal["therapist", "supervisor", "admin_clinical"]


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    display_name: str
    role: UserRoleValue
    locale: str
    is_active: bool
    created_at: datetime


class AdminUserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=255)
    role: ProfessionalRoleValue = "therapist"


class AdminRoleUpdate(BaseModel):
    role: UserRoleValue


@router.get("", response_model=list[AdminUserOut])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """List application users for the clinical-administration screen."""
    del admin  # authorization is performed by the dependency above
    return db.query(User).order_by(User.created_at.desc()).all()


@router.post("", response_model=AdminUserOut, status_code=status.HTTP_201_CREATED)
def provision_user(
    payload: AdminUserCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Create a non-patient account through the internal provisioning path."""
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        display_name=payload.display_name.strip(),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Keep the same baseline processing-consent record used by public/demo
    # provisioning.  Professional accounts do not receive patient-only state.
    db.add(Consent(user_id=user.id, consent_type="data_processing", granted=True))
    db.commit()

    audit.log(
        db,
        actor_id=admin.id,
        actor_role=admin.role,
        action="professional_user_provisioned",
        entity_type="user",
        entity_id=user.id,
        extra={"role": user.role},
    )
    return AdminUserOut.model_validate(user)


@router.put("/{user_id}/role", response_model=AdminUserOut)
def change_user_role(
    user_id: uuid.UUID,
    payload: AdminRoleUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Promote or demote an existing user while preserving their stored data."""
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if target.id == admin.id and target.role != payload.role:
        # Prevent an administrator from accidentally locking themselves out of
        # the only UI that can repair role assignments.
        raise HTTPException(status_code=400, detail="You cannot change your own administrative role")

    previous_role = target.role
    if previous_role == payload.role:
        return AdminUserOut.model_validate(target)

    target.role = payload.role

    # A user moved back to the patient experience must have the patient-only
    # state expected by SafetyPlanPage.  Promotion never deletes patient data.
    if payload.role == "patient":
        safety_plan = db.query(SafetyPlan).filter(SafetyPlan.user_id == target.id).first()
        if not safety_plan:
            db.add(SafetyPlan(user_id=target.id))

    db.commit()
    db.refresh(target)

    audit.log(
        db,
        actor_id=admin.id,
        actor_role=admin.role,
        action="user_role_changed",
        entity_type="user",
        entity_id=target.id,
        extra={"previous_role": previous_role, "new_role": target.role},
    )
    return AdminUserOut.model_validate(target)
