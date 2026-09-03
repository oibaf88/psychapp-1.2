"""Mobile telemetry ingest. Retired: those tables are empty and scheduled for drop."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/metrics", tags=["metrics"])


@router.post("/biometrics")
def submit_biometric_data():
    raise HTTPException(
        status_code=410,
        detail="La telemetría biométrica móvil se ha retirado. No se aceptan envíos.",
    )


@router.post("/app-usage")
def submit_app_usage_data():
    raise HTTPException(
        status_code=410,
        detail="La telemetría de uso de apps se ha retirado. No se aceptan envíos.",
    )
