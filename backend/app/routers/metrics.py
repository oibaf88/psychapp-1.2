import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, BiometricData, AppUsageData
from app.schemas import BiometricDataIn, BiometricDataOut, AppUsageDataIn, AppUsageDataOut
from app.security import get_current_user
from app.services import audit

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])

@router.post("/biometrics", response_model=BiometricDataOut)
def submit_biometric_data(
    payload: BiometricDataIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != "patient":
        raise HTTPException(status_code=403, detail="Only patients can submit biometric data")

    data = BiometricData(
        user_id=user.id,
        **payload.model_dump()
    )
    db.add(data)
    db.commit()
    db.refresh(data)

    audit.log(
        db,
        actor_id=user.id,
        actor_role=user.role,
        action="biometric_data_submitted",
        entity_type="biometric_data",
        entity_id=data.id,
    )
    return data

@router.post("/app-usage", response_model=AppUsageDataOut)
def submit_app_usage_data(
    payload: AppUsageDataIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role != "patient":
        raise HTTPException(status_code=403, detail="Only patients can submit app usage data")

    data = AppUsageData(
        user_id=user.id,
        **payload.model_dump()
    )
    db.add(data)
    db.commit()
    db.refresh(data)

    audit.log(
        db,
        actor_id=user.id,
        actor_role=user.role,
        action="app_usage_data_submitted",
        entity_type="app_usage_data",
        entity_id=data.id,
    )
    return data
