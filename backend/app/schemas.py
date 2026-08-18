import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field, field_serializer


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------- auth ----
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str
    # Public signup always creates a patient. This field is kept only so
    # older clients that still send it do not fail validation.
    role: str = "patient"


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
    # Public/mock Google signup also creates a patient.
    role: str = "patient"




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
    correlation_id: Optional[uuid.UUID] = None


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
    # Why this alert exists, in the therapist's terms, plus the text or
    # declaration behind it. Without these an alert is unauditable.
    related_assessment_id: Optional[uuid.UUID] = None
    rule_code: Optional[str] = None
    rule_title: Optional[str] = None
    driver_family: Optional[str] = None
    driver_family_label: Optional[str] = None
    plain_explanation: Optional[str] = None
    what_now: Optional[str] = None
    evidence: Optional[dict[str, Any]] = None

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
    correlation_id: Optional[uuid.UUID] = None
    agent2_trace_id: Optional[uuid.UUID] = None
    linguistic_signal_id_used: Optional[uuid.UUID] = None
    calculation_trace: Optional[Any] = None

    class Config:
        from_attributes = True
        # ``model_version`` is part of the deterministic risk-engine contract,
        # not a Pydantic model helper. Explicitly allow that field name so
        # production startup stays warning-free.
        protected_namespaces = ()

    @field_serializer("calculated_at")
    def serialize_calculated_at(self, value: datetime) -> str:
        return _utc_iso(value) or ""


class SignalOut(BaseModel):
    id: uuid.UUID
    signal_type: str
    value: Any
    confidence_band: Optional[str] = None
    timestamp: datetime
    agent2_trace_id: Optional[uuid.UUID] = None

    class Config:
        from_attributes = True

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return _utc_iso(value) or ""


class Agent2AnalysisTraceOut(BaseModel):
    id: uuid.UUID
    correlation_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    source_text: str
    status: str
    provider: str
    requested_model: str
    response_model: Optional[str] = None
    effort: str
    max_tokens: int
    prompt_version: str
    prompt_sha256: str
    schema_version: str
    schema_sha256: str
    provider_message_id: Optional[str] = None
    provider_request_id: Optional[str] = None
    stop_reason: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    error_kind: Optional[str] = None
    error_code: Optional[str] = None
    http_status: Optional[int] = None
    app_release: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    analysis: Optional[Any] = None
    signal_id: Optional[uuid.UUID] = None
    risk_assessment_id: Optional[uuid.UUID] = None
    used_by_risk_engine: bool = False

    @field_serializer("started_at", "completed_at")
    def serialize_trace_timestamps(self, value: datetime | None) -> str | None:
        return _utc_iso(value)


# ------------------------------------------------- clinical explanations ---
class PatientChatMessageOut(BaseModel):
    """A turn of the patient's own conversation with Agent 1.

    Exposed to the assigned professional because chat, like the diary, is a
    source the risk pipeline reads: a therapist cannot audit an alert raised
    from a chat message without being able to read that message.
    """

    id: uuid.UUID
    role: str
    content: str
    ui_mode: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _utc_iso(value) or ""


class LevelExplanationOut(BaseModel):
    level: Optional[int] = None
    level_label: str
    level_meaning: str
    headline: str
    rule_code: Optional[str] = None
    rule_title: Optional[str] = None
    rule_explanation: Optional[str] = None
    driver_family: str
    driver_family_label: str
    driver_evidence_kind: Optional[str] = None
    what_now: Optional[str] = None
    structural_reconciliation: Optional[str] = None
    driver_evidence: Optional[dict[str, Any]] = None
    calculated_at: Optional[str] = None
    assessment_id: Optional[str] = None
    generated_alert_id: Optional[str] = None


class StructuralVariableOut(BaseModel):
    key: str
    label: str
    note: Optional[str] = None
    baseline_mean: Optional[float] = None
    baseline_std: Optional[float] = None
    recent_mean: Optional[float] = None
    difference: Optional[float] = None
    z_score: Optional[float] = None
    abs_z: Optional[float] = None
    direction: str
    reading: str


class StructuralExplanationOut(BaseModel):
    score: Optional[float] = None
    band: Optional[str] = None
    band_label: Optional[str] = None
    band_meaning: Optional[str] = None
    scale_note: str
    summary: str
    direction_summary: Optional[str] = None
    variables: list[StructuralVariableOut] = []
    composite_z: Optional[float] = None
    adverse_composite_z: Optional[float] = None
    favourable_composite_z: Optional[float] = None
    baseline_sample_count: Optional[int] = None
    recent_sample_count: Optional[int] = None
    sleep_trend: Optional[str] = None
    sleep_trend_slope: Optional[float] = None
    caveats: list[str] = []


class EvidenceItemOut(BaseModel):
    """One analysed text and everything the system concluded from it."""

    trace_id: str
    correlation_id: str
    source_type: str
    source_label: str
    source_id: Optional[str] = None
    source_text: str
    source_excerpt: str
    source_created_at: Optional[str] = None
    analysed_at: Optional[str] = None
    status: str
    analysis: Optional[dict[str, Any]] = None
    flags: list[str] = []
    reading: str
    short_rationale: Optional[str] = None
    signal_id: Optional[str] = None
    assessment_id: Optional[str] = None
    resulting_level: Optional[int] = None
    resulting_rule: Optional[str] = None
    used_by_risk_engine: bool = False
    alert_id: Optional[str] = None
    alert_level: Optional[int] = None
    alert_status: Optional[str] = None
    alert_title: Optional[str] = None


class PsychosocialDomainOut(BaseModel):
    domain: str
    label: str
    category: str
    category_label: str
    valence: str
    intensity: float
    confidence: float
    status: str
    summary: str
    quote: str
    observed_at: Optional[str] = None
    observation_id: str
    weight: float
    contribution: float
    is_change: bool
    group: Optional[str] = None
    group_label: Optional[str] = None
    risk_value: Optional[float] = None
    counts_for_scoring: bool = True
    is_stale: bool = False
    has_pending_update: bool = False
    session_question: Optional[str] = None


class PsychosocialIndexReadingOut(BaseModel):
    """One of the four indices, with the threshold it is read against."""

    key: str
    label: str
    value: Optional[float] = None
    state: str  # ok | alerta | sin_datos
    threshold: float
    threshold_label: str
    meaning: str
    note: str


class PsychosocialIndicesOut(BaseModel):
    """None, never 0.0: absence of data is not evidence of safety."""

    support_index: Optional[float] = None
    material_adversity_index: Optional[float] = None
    interpersonal_risk_index: Optional[float] = None
    relapse_context_index: Optional[float] = None


class PsychosocialLeaveTakingOut(BaseModel):
    domain: str
    label: Optional[str] = None
    category: str
    category_label: Optional[str] = None
    summary: Optional[str] = None
    quote: Optional[str] = None
    observed_at: Optional[str] = None
    observation_id: Optional[str] = None


class PsychosocialSessionQuestionOut(BaseModel):
    domain: str
    domain_label: str
    question: str
    reason: str
    quote: Optional[str] = None


class PsychosocialAcuteChangeOut(BaseModel):
    domain: str
    label: str
    category: str
    category_label: str
    summary: str
    quote: str
    observed_at: Optional[str] = None
    observation_id: str


class PsychosocialExplanationOut(BaseModel):
    """The social-determinants view: index, per-domain breakdown, quotes."""

    index: Optional[float] = None
    band: str
    band_label: str
    scale_note: str
    summary: str
    driver_summary: Optional[str] = None
    protective_summary: Optional[str] = None
    domains: list[PsychosocialDomainOut] = []
    acute_changes: list[PsychosocialAcuteChangeOut] = []
    has_acute_change: bool = False
    acute_note: Optional[str] = None
    caveats: list[str] = []
    indices: PsychosocialIndicesOut = PsychosocialIndicesOut()
    index_readings: list[PsychosocialIndexReadingOut] = []
    leave_taking: Optional[PsychosocialLeaveTakingOut] = None
    leave_taking_note: Optional[str] = None
    interpersonal_recent_evidence: list[str] = []
    pending_update_domains: list[str] = []
    stale_domains: list[str] = []
    session_questions: list[PsychosocialSessionQuestionOut] = []
    observation_count: int = 0
    active_count: int = 0
    confirmed_count: int = 0
    refuted_count: int = 0


class PsychosocialObservationOut(BaseModel):
    id: uuid.UUID
    domain: str
    domain_label: str
    category: str
    category_label: str
    valence: str
    intensity: float
    confidence: float
    is_change: bool
    status: str
    summary: str
    evidence_quote: str
    source_type: str
    source_label: str
    source_id: Optional[uuid.UUID] = None
    adjudication_note: Optional[str] = None
    adjudicated_at: Optional[str] = None
    observed_at: Optional[str] = None


class PsychosocialAdjudicationIn(BaseModel):
    status: str = Field(pattern="^(confirmed|refuted|inferred)$")
    note: Optional[str] = Field(default=None, max_length=1000)


class PatientMetricsOut(BaseModel):
    window_days: int
    generated_at: Optional[str] = None
    checkins: list[dict[str, Any]] = []
    structural: list[dict[str, Any]] = []
    daily_structural: list[dict[str, Any]] = []
    psychosocial: list[dict[str, Any]] = []
    daily_psychosocial: list[dict[str, Any]] = []
    psychosocial_events: list[dict[str, Any]] = []
    linguistic: list[dict[str, Any]] = []
    levels: list[dict[str, Any]] = []
    daily_levels: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    counts: dict[str, int] = {}


# ------------------------------------------------------ therapist copilot ---
class CopilotAskIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    window_days: int = Field(default=60, ge=7, le=365)


class CopilotSummaryIn(BaseModel):
    window_days: int = Field(default=60, ge=7, le=365)


class CopilotMessageOut(BaseModel):
    id: uuid.UUID
    patient_id: uuid.UUID
    role: str
    content: str
    kind: str
    requested_model: Optional[str] = None
    context_window_days: Optional[int] = None
    context_counts: Optional[dict[str, Any]] = None
    error_kind: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return _utc_iso(value) or ""


class PatientDossierOut(BaseModel):
    """Full clinical history for assigned therapist/supervisor — independent of alerts."""

    patient: PatientSummaryOut
    current_risk: Optional[RiskAssessmentOut] = None
    level_explanation: LevelExplanationOut
    structural_explanation: StructuralExplanationOut
    psychosocial_explanation: PsychosocialExplanationOut
    metrics: PatientMetricsOut
    evidence: list[EvidenceItemOut] = []
    timeline: TimelineOut
    checkins: list[CheckInOut]
    diary: list[DiaryOut]
    chat_messages: list[PatientChatMessageOut] = []
    facts: list[FactOut]
    assessments: list[RiskAssessmentOut]
    alerts: list[AlertOut]
    signals: list[SignalOut]
    agent2_traces: list[Agent2AnalysisTraceOut]
    deep_analysis: Optional[DeepStatisticalAnalysisOut] = None
    safety_plan: Optional[SafetyPlanOut] = None
    professional_protocol: dict[str, str]
