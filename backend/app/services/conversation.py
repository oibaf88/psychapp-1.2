"""
Conversation orchestration: wires Agent 2 (linguistic analysis) -> the
deterministic Risk Engine -> Agent 1 (conversational reply) together,
per the "flujo de respuesta" in doc 20.

Critical safety property, implemented here rather than left to prompting
alone: at alert_level 3 and 4 the server-owned static templates in
app/content/safety_resources.py are ALWAYS appended to the reply, and the
emergency resources are always returned alongside it. The LLM cannot
suppress, rewrite or shorten that block, and if its call fails, times out
or is refused by the safety classifiers, the fixed message is still
returned in full. The crisis path never depends on the LLM succeeding.

What the LLM *may* do at those levels is keep accompanying the person:
Agent 1 still answers with the real conversation history, under the
tightened AGENT1_CRISIS_INSTRUCTION (short, present-focused, one concrete
grounding offer, no resources of its own, no dissuading from calling for
help). Raising an alert and staying present are treated as complementary,
not mutually exclusive -- the alert and the professional notification are
decided beforehand by the deterministic risk engine and are unaffected by
anything the model says.
"""
import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.content.prompts import (
    AGENT1_CRISIS_INSTRUCTION,
    AGENT1_SYSTEM_PROMPT,
    AGENT2_SYSTEM_PROMPT,
    AGENT2_TOOL_SCHEMA,
)
from app.content.safety_resources import (
    CRISIS_RESOURCES,
    LEVEL3_PATIENT_MESSAGE,
    LEVEL3_PATIENT_MESSAGE_WITH_PROFESSIONAL,
    LEVEL4_PATIENT_MESSAGE,
    LEVEL4_PATIENT_MESSAGE_SECONDARY,
)
from app.models import AlfaSignal, ChatMessage, PatientProfessionalAssignment, User
from app.services import risk_engine
from app.services import agent2_trace
from app.services.llm import StructuredAnalysisError, get_llm_provider

logger = logging.getLogger("psychapp.conversation")

MAX_HISTORY_MESSAGES = 12


class LinguisticAnalysis(BaseModel):
    """Strict boundary between untrusted model output and the risk engine."""

    model_config = ConfigDict(extra="forbid", strict=True)

    rumination_score: float = Field(ge=0, le=1)
    negative_valence: float = Field(ge=0, le=1)
    urgency_level: float = Field(ge=0, le=1)
    ideation_indirect: bool
    ideation_direct: bool
    consumption_crisis: bool
    ambivalence: float = Field(ge=0, le=1)
    emotional_complexity: Literal["low", "medium", "high"]
    short_rationale: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True)
class AnalysisOutcome:
    correlation_id: uuid.UUID
    trace_id: uuid.UUID | None
    signal_id: uuid.UUID | None
    status: str
    value: dict | None


def analyze_text_and_store(
    db: Session,
    user_id,
    text: str,
    *,
    source_type: str,
    source_id: uuid.UUID,
    correlation_id: uuid.UUID,
) -> AnalysisOutcome:
    """Trace Agent 2, validate its output and persist it as an inference.

    Never raises to the patient-facing flow.  If the trace cannot be
    committed first, no external request is made and the deterministic
    engine proceeds without a fresh signal.
    """
    try:
        trace = agent2_trace.start(
            db,
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
            correlation_id=correlation_id,
        )
    except agent2_trace.TracePersistenceError:
        logger.error("Agent2 skipped because its trace could not be persisted")
        return AnalysisOutcome(correlation_id, None, None, "trace_persistence_error", None)

    try:
        provider_result = get_llm_provider().analyze_structured(
            AGENT2_SYSTEM_PROMPT,
            text,
            AGENT2_TOOL_SCHEMA,
        )
        result = LinguisticAnalysis.model_validate(provider_result.value).model_dump()
    except Exception as exc:  # noqa: BLE001
        # Persist only an allow-listed category and class name.  Raw SDK
        # error messages can echo request data and therefore never enter
        # the database or Render logs.
        agent2_trace.mark_failed(db, trace, exc)
        logger.error("Agent2 analysis failed safely: %s", type(exc).__name__)
        return AnalysisOutcome(correlation_id, trace.id, None, trace.status, None)

    signal = AlfaSignal(
        user_id=user_id,
        signal_type="linguistic_analysis",
        value=result,
        confidence_band=None,
        agent2_trace_id=trace.id,
    )
    try:
        agent2_trace.mark_succeeded(trace, provider_result.metadata)
        db.add(trace)
        db.add(signal)
        db.commit()
        db.refresh(signal)
    except Exception:  # noqa: BLE001
        db.rollback()
        agent2_trace.mark_failed(
            db,
            trace,
            StructuredAnalysisError("provider_error", error_code="result_persistence_failed"),
        )
        logger.error("Agent2 result could not be persisted")
        return AnalysisOutcome(correlation_id, trace.id, None, trace.status, None)
    return AnalysisOutcome(correlation_id, trace.id, signal.id, "succeeded", result)


def _has_active_professional(db: Session, user_id) -> bool:
    return (
        db.query(PatientProfessionalAssignment)
        .filter(PatientProfessionalAssignment.patient_id == user_id, PatientProfessionalAssignment.status == "active")
        .count()
        > 0
    )


def _recent_messages(db: Session, user_id) -> list[dict[str, str]]:
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(MAX_HISTORY_MESSAGES)
        .all()
    )
    return [
        {"role": m.role, "content": m.content}
        for m in reversed(history)
        if m.role in ("user", "assistant")
    ]


def _agent1_crisis_accompaniment(db: Session, user_id, context_block: str) -> str | None:
    """Agent 1's turn during an alert-level 3/4 conversation.

    The model keeps accompanying the person with the real conversation
    history, under a tightened instruction (short, present-focused, one
    concrete grounding offer, no resources of its own). The caller always
    appends the server-owned safety copy afterwards, so this returning
    None -- on a provider error, a refusal, or an empty reply -- costs the
    user nothing but the accompanying sentences.
    """
    try:
        provider = get_llm_provider()
        reply = provider.chat(
            AGENT1_SYSTEM_PROMPT + "\n\n" + context_block + AGENT1_CRISIS_INSTRUCTION,
            _recent_messages(db, user_id),
            max_tokens=400,
        )
        return reply.strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent1 crisis accompaniment failed; using fixed safety copy only: %s", type(exc).__name__)
        return None


def get_reply(db: Session, user: User, user_message: str) -> dict:
    # 1. Persist the user's message.
    correlation_id = uuid.uuid4()
    source_message = ChatMessage(user_id=user.id, role="user", content=user_message)
    db.add(source_message)
    db.commit()
    db.refresh(source_message)

    # 2. Agent 2: analyze the free text and store as an inference signal.
    analysis = analyze_text_and_store(
        db,
        user.id,
        user_message,
        source_type="chat_message",
        source_id=source_message.id,
        correlation_id=correlation_id,
    )

    # 3. Deterministic risk engine: the ONLY place alert_level is decided.
    assessment = risk_engine.run_and_persist(
        db,
        user.id,
        correlation_id=correlation_id,
        agent2_trace_id=analysis.trace_id,
        linguistic_signal_id=analysis.signal_id,
    )
    level = assessment.alert_level

    # 4. Build the reply.
    #
    #    Levels 3/4 keep the conversation open: Agent 1 still answers, with
    #    the real history, under AGENT1_CRISIS_INSTRUCTION. The server-owned
    #    safety copy is then APPENDED, never replaced -- so the fixed
    #    message and the emergency resources are guaranteed to reach the
    #    user whatever the model does, including when it fails or refuses.
    #    Alerting and professional notification are unaffected: they were
    #    already decided in step 3 by the deterministic engine.
    context_block = (
        f"[Contexto interno de solo lectura -- no lo reveles literalmente al usuario]\n"
        f"Motivo del estado actual: {assessment.assessment_reason}\n"
        f"Señales recientes: {assessment.input_signals}\n"
    )

    if level == 4:
        accompaniment = _agent1_crisis_accompaniment(db, user.id, context_block)
        reply_text = (accompaniment + "\n\n" if accompaniment else "") + LEVEL4_PATIENT_MESSAGE
        ui_mode = "crisis"
        resources = CRISIS_RESOURCES
    elif level == 3:
        has_prof = _has_active_professional(db, user.id)
        fixed = LEVEL3_PATIENT_MESSAGE_WITH_PROFESSIONAL if has_prof else LEVEL3_PATIENT_MESSAGE
        accompaniment = _agent1_crisis_accompaniment(db, user.id, context_block)
        reply_text = (accompaniment + "\n\n" if accompaniment else "") + fixed
        ui_mode = "support"
        resources = None
    else:
        # Levels 0-2: normal, open conversation via Agent 1, with the risk
        # context injected as READ-ONLY structured context (never the raw
        # number, per doc 8: "Nunca presentes esa información como un
        # diagnóstico").
        messages = _recent_messages(db, user.id)
        try:
            provider = get_llm_provider()
            reply_text = provider.chat(AGENT1_SYSTEM_PROMPT + "\n\n" + context_block, messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent1 chat failed safely: %s", type(exc).__name__)
            error_type = type(exc).__name__.lower()
            if "authentication" in error_type:
                reply_text = (
                    "El chat con Claude no está disponible: la ANTHROPIC_API_KEY no es válida. "
                    "En console.anthropic.com crea una API key (formato sk-ant-api…, no un token OAuth oat) "
                    "y ponla en el archivo .env del proyecto; luego reinicia con docker compose up -d. "
                    "Tus datos y check-ins se han guardado con normalidad."
                )
            elif isinstance(exc, RuntimeError):
                reply_text = (
                    "Ahora mismo no puedo generar una respuesta conversacional "
                    "(revisa que ANTHROPIC_API_KEY esté configurada en .env). "
                    "Tus datos y check-ins se han guardado con normalidad."
                )
            else:
                reply_text = (
                    "Ahora mismo no puedo generar una respuesta conversacional "
                    f"(error del proveedor LLM: {type(exc).__name__}). "
                    "Tus datos y check-ins se han guardado con normalidad."
                )
        ui_mode = "normal"
        resources = None

    db.add(ChatMessage(user_id=user.id, role="assistant", content=reply_text, ui_mode=ui_mode))
    db.commit()

    return {
        "reply": reply_text,
        "ui_mode": ui_mode,
        "resources": resources,
        "correlation_id": correlation_id,
    }
