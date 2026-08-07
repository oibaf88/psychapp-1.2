import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------- auth ----
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str
    role: str = "patient"  # patient | therapist | supervisor | admin_clinical


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    display_name: str
    role: str
    locale: str

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequest(BaseModel):
    email: EmailStr

class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class GoogleLoginRequest(BaseModel):
    id_token: str
    role: str = "patient" # Optional role if user is new




# ------------------------------------------------------------- consents ----
class ConsentIn(BaseModel):
    consent_type: str
    granted: bool = True


class ConsentOut(BaseModel):
    id: uuid.UUID
    consent_type: str
    granted: bool
    version: str
    granted_at: datetime
    revoked_at: Optional[datetime]

    class Config:
        from_attributes = True



# ------------------------------------------------------------- metrics ---
class BiometricDataIn(BaseModel):
    device_type: str
    heart_rate_avg: Optional[float] = None
    heart_rate_variability: Optional[float] = None
    sleep_duration_hours: Optional[float] = None
    sleep_quality_score: Optional[float] = None
    deep_sleep_hours: Optional[float] = None
    rem_sleep_hours: Optional[float] = None
    steps: Optional[int] = None
    active_calories: Optional[float] = None
    measured_at: datetime = Field(default_factory=datetime.utcnow)

class BiometricDataOut(BiometricDataIn):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class AppUsageDataIn(BaseModel):
    apps_usage_stats: dict
    screen_time_minutes: Optional[int] = None
    measured_at: datetime = Field(default_factory=datetime.utcnow)

class AppUsageDataOut(AppUsageDataIn):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True

class DeepStatisticalAnalysisOut(BaseModel):
    biometrics: list[BiometricDataOut]
    app_usage: list[AppUsageDataOut]
    insights: list[str]


# ------------------------------------------------------------- check-ins ---
class CheckInIn(BaseModel):
    mood: int = Field(ge=0, le=10)
    craving: int = Field(ge=0, le=10)
    sleep_hours: float = Field(ge=0, le=24)
    self_efficacy: int = Field(ge=0, le=10)
    notes: Optional[str] = None


class CheckInOut(CheckInIn):
    id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------------------- diary -----
class DiaryIn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class DiaryOut(BaseModel):
    id: uuid.UUID
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


# ---------------------------------------------------------- safety plan ---
class SafetyPlanIn(BaseModel):
    warning_signs: Optional[str] = None
    coping_strategies: Optional[str] = None
    social_supports: Optional[str] = None
    professional_contacts: Optional[str] = None
    safe_environment: Optional[str] = None
    reasons_to_live: Optional[str] = None


class SafetyPlanOut(SafetyPlanIn):
    id: uuid.UUID
    updated_at: datetime

    class Config:
        from_attributes = True


# ------------------------------------------------------------- timeline ----
class TimelinePoint(BaseModel):
    date: str
    mood: Optional[float] = None
    craving: Optional[float] = None
    sleep_hours: Optional[float] = None
    self_efficacy: Optional[float] = None
    structural_score: Optional[float] = None
    confidence_band: Optional[str] = None


class TimelineOut(BaseModel):
    points: list[TimelinePoint]
    baseline_available: bool
    window_days: int


# ---------------------------------------------------------------- chat -----
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatOut(BaseModel):
    reply: str
    ui_mode: str  # normal | support | crisis
    resources: Optional[list[dict[str, str]]] = None


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    ui_mode: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ------------------------------------------------------------- facts -------
class FactIn(BaseModel):
    category: str
    content: str


class FactOut(BaseModel):
    id: uuid.UUID
    category: str
    content: str
    declared_by: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


# --------------------------------------------------------- professional ---
class AssignmentRequestIn(BaseModel):
    patient_email: EmailStr


class AssignmentOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    professional_id: uuid.UUID
    status: str
    requested_at: datetime
    updated_at: Optional[datetime] = None
    patient_email: Optional[str] = None
    patient_display_name: Optional[str] = None
    professional_email: Optional[str] = None
    professional_display_name: Optional[str] = None

    class Config:
        from_attributes = True


class AlertOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    alert_level: int
    status: str
    title: str
    description: str
    related_signals: Optional[Any] = None
    created_at: datetime
    acknowledged_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    dismiss_reason: Optional[str] = None
    patient_display_name: Optional[str] = None
    patient_email: Optional[str] = None

    class Config:
        from_attributes = True


class AlertResolveIn(BaseModel):
    resolution_notes: str


class AlertDismissIn(BaseModel):
    dismiss_reason: str


class PatientSummaryOut(BaseModel):
    id: uuid.UUID
    display_name: str
    email: EmailStr
    assignment_status: str
    latest_alert_level: Optional[int] = None
    latest_structural_score: Optional[float] = None
    latest_confidence_band: Optional[str] = None
    open_alerts: int = 0
    checkin_count: int = 0
    last_checkin_at: Optional[datetime] = None


class RiskAssessmentOut(BaseModel):
    id: uuid.UUID
    alert_level: int
    triggering_rules: Any
    input_signals: Any
    input_facts: Optional[Any] = None
    confidence: Optional[float] = None
    assessment_reason: str
    model_version: str
    calculated_at: datetime
    generated_alert_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True


class SignalOut(BaseModel):
    id: uuid.UUID
    signal_type: str
    value: Any
    confidence_band: Optional[str] = None
    timestamp: datetime

    class Config:
        from_attributes = True


class PatientDossierOut(BaseModel):
    """Full clinical history for assigned therapist/supervisor — independent of alerts."""

    patient: PatientSummaryOut
    current_risk: Optional[RiskAssessmentOut] = None
    timeline: TimelineOut
    checkins: list[CheckInOut]
    diary: list[DiaryOut]
    facts: list[FactOut]
    assessments: list[RiskAssessmentOut]
    alerts: list[AlertOut]
    signals: list[SignalOut]
    deep_analysis: Optional[DeepStatisticalAnalysisOut] = None
    safety_plan: Optional[SafetyPlanOut] = None
    professional_protocol: dict[str, str]
