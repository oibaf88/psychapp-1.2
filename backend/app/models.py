"""
SQLAlchemy models for PsychApp.

This schema is a pragmatic, buildable simplification of the data model
described across the spec docs (docs 1, 11, 14, 16, 17, 18), in particular:

  - Users (patient / professional / admin) instead of a full identity /
    pseudonymization service (see README "Assumptions" for what was
    simplified).
  - `confirmed_facts` vs `alfa_signals` implements the "Muro de Hechos vs
    Inferencias" (Fact/Inference wall, doc 6 & 20): facts are only ever
    written by a user or professional action; signals are only ever
    written by the analytic/LLM services and can be superseded by a fact
    but never the reverse.
  - `risk_assessments` / `professional_alerts` / `notifications` follow the
    deterministic risk engine contract from doc 17/18 verbatim (columns,
    JSON fields, alert_level 0-4, model_version, triggering_rules).
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def uuid_pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class UserRole(str, enum.Enum):
    patient = "patient"
    therapist = "therapist"
    supervisor = "supervisor"
    admin_clinical = "admin_clinical"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default=UserRole.patient.value)
    locale: Mapped[str] = mapped_column(String(10), default="es-ES")
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "role IN ('patient','therapist','supervisor','admin_clinical')",
            name="ck_users_role",
        ),
    )


class PasswordResetToken(Base):
    """Single-use, time-limited password reset token.

    Referenced by app/routers/auth.py. The router shipped without this
    model, which made `from app.models import ... PasswordResetToken`
    fail and prevented the whole API from importing.
    """

    __tablename__ = "password_reset_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Consent(Base):
    __tablename__ = "user_consents"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    consent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # data_processing | professional_sharing | crisis_sms | research
    version: Mapped[str] = mapped_column(String(32), default="v1")
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PatientProfessionalAssignment(Base):
    __tablename__ = "patient_professional_assignments"

    id: Mapped[uuid.UUID] = uuid_pk()
    patient_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    professional_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending | active | paused | ended | rejected
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ConfirmedFact(Base):
    """
    Facts are the only thing an LLM may read and never overwrite (doc 6 /
    doc 20: "Todo lo que el usuario o el profesional confirman se guarda
    como Hecho. El LLM nunca puede modificar los hechos.").
    """

    __tablename__ = "confirmed_facts"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    # medication_taken | relapse | consumption_crisis | ideation_active |
    # planning | correction | other
    content: Mapped[str] = mapped_column(Text, nullable=False)
    declared_by: Mapped[str] = mapped_column(String(16), nullable=False)  # user | professional
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AlfaSignal(Base):
    """
    System-computed signals ("inferences"), including the local
    structural_score/confidence_band equivalent of the "Alfa ML" engine
    described in the docs, and the structured output of the Agent 2
    linguistic analyzer. Never written by a human, never authoritative
    over a ConfirmedFact.
    """

    __tablename__ = "alfa_signals"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # structural_score | linguistic_rumination | linguistic_valence |
    # linguistic_urgency | linguistic_ideation | sleep_trend | checkin_trend
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    confidence_band: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # stable | transition | unstable | insufficient_data
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_fact: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Baseline(Base):
    __tablename__ = "baselines"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime)
    stats: Mapped[dict] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BiometricData(Base):
    """Passively collected wearable measurements.

    Columns mirror BiometricDataIn in app/schemas.py exactly, because
    app/routers/metrics.py constructs this with **payload.model_dump().
    """

    __tablename__ = "biometric_data"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    device_type: Mapped[str] = mapped_column(String(64), nullable=False)
    heart_rate_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    heart_rate_variability: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    deep_sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    rem_sleep_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppUsageData(Base):
    """Passively collected phone-usage statistics.

    Columns mirror AppUsageDataIn in app/schemas.py exactly, because
    app/routers/metrics.py constructs this with **payload.model_dump().
    """

    __tablename__ = "app_usage_data"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    apps_usage_stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    screen_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CheckIn(Base):
    __tablename__ = "check_ins"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    mood: Mapped[int] = mapped_column(Integer)  # 0-10
    craving: Mapped[int] = mapped_column(Integer)  # 0-10
    sleep_hours: Mapped[float] = mapped_column(Float)
    self_efficacy: Mapped[int] = mapped_column(Integer)  # 0-10 (EAG-style item)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DiaryEntry(Base):
    __tablename__ = "diary_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    alert_level: Mapped[int] = mapped_column(Integer, nullable=False)
    triggering_rules: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_signals: Mapped[dict] = mapped_column(JSON, nullable=False)
    input_facts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    assessment_reason: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(32), default="risk-engine-v1.0")
    calculated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    generated_alert_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    __table_args__ = (CheckConstraint("alert_level BETWEEN 0 AND 4", name="ck_risk_alert_level"),)


class ProfessionalAlert(Base):
    __tablename__ = "professional_alerts"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    alert_level: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open")
    # open | acknowledged | resolved | dismissed
    source: Mapped[str] = mapped_column(String(32), default="rule_engine")
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    related_signals: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    related_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    professional_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    recipient_type: Mapped[str] = mapped_column(String(16), nullable=False)  # patient | professional
    channel: Mapped[str] = mapped_column(String(16), nullable=False)  # in_app | email
    alert_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    template_code: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending | sent | delivered | failed | read
    related_alert_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    related_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SafetyPlan(Base):
    __tablename__ = "safety_plans"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), unique=True, nullable=False)
    warning_signs: Mapped[str | None] = mapped_column(Text, nullable=True)
    coping_strategies: Mapped[str | None] = mapped_column(Text, nullable=True)
    social_supports: Mapped[str | None] = mapped_column(Text, nullable=True)
    professional_contacts: Mapped[str | None] = mapped_column(Text, nullable=True)
    safe_environment: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasons_to_live: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ui_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)  # normal|support|crisis
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extra: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
