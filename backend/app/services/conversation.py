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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.content.prompts import (
    AGENT1_CRISIS_INSTRUCTION,
    AGENT1_SYSTEM_PROMPT,
    ANALYZER_SYSTEM_PROMPT,
    ANALYZER_TOOL_SCHEMA,
)
from app.content.safety_resources import (
    CRISIS_RESOURCES,
    LEVEL3_PATIENT_MESSAGE,
    LEVEL3_PATIENT_MESSAGE_WITH_PROFESSIONAL,
    LEVEL4_PATIENT_MESSAGE,
    LEVEL4_PATIENT_MESSAGE_SECONDARY,
)
from app.models import AlfaSignal, ChatMessage, PatientProfessionalAssignment, User
from app.services import llm_config, profile as profile_service, psychosocial, risk_engine
from app.services import agent2_trace
from app.services.llm import ChatResult, ProviderMetadata, StructuredAnalysisError, get_llm_provider

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
    # How this reading sits against the patient's own history, as the model
    # judged it. Defaulted rather than required so a provider that has not
    # seen the new schema — or a stored signal written before it existed —
    # still validates: absent means "no comparison was made", which is
    # exactly what those cases mean.
    deviation_from_own_baseline: Literal[
        "unknown", "much_lower", "lower", "typical", "higher", "much_higher"
    ] = "unknown"
    is_typical_for_patient: bool = True


@dataclass(frozen=True)
class AnalysisOutcome:
    correlation_id: uuid.UUID
    trace_id: uuid.UUID | None
    signal_id: uuid.UUID | None
    status: str
    value: dict | None
    # The psychosocial half of the same call. Empty when the text was too
    # short to carry social context, or when only that half failed to
    # validate — which is why it is reported separately from `status`.
    observation_ids: list[uuid.UUID] = field(default_factory=list)
    psychosocial_status: str = "not_attempted"


def analyze_text_and_store(
    db: Session,
    user_id,
    text: str,
    *,
    source_type: str,
    source_id: uuid.UUID,
    correlation_id: uuid.UUID,
    observed_at: datetime | None = None,
) -> AnalysisOutcome:
    """Analyse one piece of patient text, once, and persist both readings.

    This used to be two provider calls over the same text — Agent 2 for the
    linguistic markers, Agent 4 for the social determinants — each with its
    own trace. They never disagreed about anything, because their schemas
    were disjoint by construction; they simply cost twice. Now one call
    returns both blocks under one trace.

    Never raises to the patient-facing flow. If the trace cannot be
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
            agent_role=agent2_trace.ANALYZER_ROLE,
        )
    except agent2_trace.TracePersistenceError:
        logger.error("Analysis skipped because its trace could not be persisted")
        return AnalysisOutcome(correlation_id, None, None, "trace_persistence_error", None)

    # Who the analyser is reading. Read-only, and read without creating: a
    # patient with no profile is analysed exactly as before this existed.
    patient_profile = profile_service.get(db, user_id)
    system_prompt = ANALYZER_SYSTEM_PROMPT + profile_service.analyzer_context_block(patient_profile)

    try:
        provider_result = get_llm_provider(db).analyze_structured(
            system_prompt,
            text,
            ANALYZER_TOOL_SCHEMA,
        )
        value = provider_result.value
        if not isinstance(value, dict):
            raise ValueError("analyzer returned a non-object")
        result = LinguisticAnalysis.model_validate(value.get("linguistic")).model_dump()
    except Exception as exc:  # noqa: BLE001
        # Persist only an allow-listed category and class name.  Raw SDK
        # error messages can echo request data and therefore never enter
        # the database or Render logs.
        agent2_trace.mark_failed(db, trace, exc)
        logger.error("Analysis failed safely: %s", type(exc).__name__)
        return AnalysisOutcome(correlation_id, trace.id, None, trace.status, None)

    # The psychosocial half is built separately and is allowed to fail on its
    # own. Losing a linguistic signal because an observation quote came back
    # malformed would trade a safety-critical input for a contextual one.
    rows, psychosocial_status = _psychosocial_rows(
        db,
        trace,
        value.get("psychosocial"),
        user_id=user_id,
        text=text,
        source_type=source_type,
        source_id=source_id,
        correlation_id=correlation_id,
        observed_at=observed_at,
    )

    signal = AlfaSignal(
        user_id=user_id,
        signal_type="linguistic_analysis",
        value=result,
        confidence_band=None,
        agent2_trace_id=trace.id,
    )
    try:
        agent2_trace.mark_succeeded(trace, provider_result.metadata)
        if psychosocial_status == "invalid_block":
            # The call succeeded; one block of it did not. Recorded on the
            # trace rather than in the status, which stays the outcome of
            # the call itself.
            trace.error_code = "psychosocial_block_invalid"
        db.add(trace)
        db.add(signal)
        for row in rows:
            db.add(row)
        db.commit()
        db.refresh(signal)
    except Exception:  # noqa: BLE001
        db.rollback()
        agent2_trace.mark_failed(
            db,
            trace,
            StructuredAnalysisError("provider_error", error_code="result_persistence_failed"),
        )
        logger.error("Analysis result could not be persisted")
        return AnalysisOutcome(correlation_id, trace.id, None, trace.status, None)
    # Fold what was learned about the person back in, after the analysis is
    # safely committed. A failure here costs the profile update only; it must
    # never roll back the signal the risk engine is about to read, and it must
    # never raise — this function's whole contract is that the patient-facing
    # flow survives whatever the analytic layer does.
    try:
        profile_service.apply_analyzer_update(db, user_id, value.get("profile_update"))
        profile_service.refresh_linguistic_baseline(db, user_id)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.warning("Profile refresh skipped safely: %s", type(exc).__name__)

    return AnalysisOutcome(
        correlation_id,
        trace.id,
        signal.id,
        "succeeded",
        result,
        observation_ids=[row.id for row in rows],
        psychosocial_status=psychosocial_status,
    )


def _psychosocial_rows(
    db: Session,
    trace,
    block,
    *,
    user_id,
    text: str,
    source_type: str,
    source_id: uuid.UUID,
    correlation_id: uuid.UUID,
    observed_at: datetime | None,
):
    """Build the psychosocial rows, or explain why there are none.

    Returns ``(rows, status)``. Never raises: a bad psychosocial block must
    not cost the linguistic signal that came back in the same response.
    """
    if not text or len(text.strip()) < psychosocial.MIN_TEXT_CHARS_FOR_EXTRACTION:
        return [], "skipped_short_text"
    if not isinstance(block, dict):
        logger.warning("Analyzer returned no usable psychosocial block")
        return [], "invalid_block"
    try:
        rows = psychosocial.build_observation_rows(
            block,
            user_id=user_id,
            text=text,
            source_type=source_type,
            source_id=source_id,
            correlation_id=correlation_id,
            trace_id=trace.id,
            observed_at=observed_at,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Psychosocial block rejected (%s); linguistic half kept", type(exc).__name__)
        return [], "invalid_block"
    return rows, "succeeded"


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


def _reply_provenance(
    db: Session,
    *,
    from_model: bool,
    metadata: ProviderMetadata | None = None,
) -> dict:
    """Which model produced an assistant turn, for the stored message.

    A turn built entirely from the server-owned safety templates has no
    model behind it, and says so by storing nothing — that distinction is
    exactly what someone re-reading a crisis conversation needs.

    Prefers the metadata the call itself came back with: that names the
    model the server said answered, where re-resolving the configuration
    only names the model the app would ask for now. On a local runtime
    those differ whenever the loaded weights are not the configured ones.
    """
    if not from_model:
        return {}
    if metadata is not None:
        return {
            "provider": metadata.provider,
            "model": metadata.response_model or metadata.requested_model,
            "provider_base_url": metadata.base_url,
        }
    active = llm_config.resolve(db)
    return {
        "provider": active.provider,
        "model": active.chat_model,
        "provider_base_url": active.base_url,
    }


def _agent1_crisis_accompaniment(db: Session, user_id, context_block: str) -> ChatResult | None:
    """Agent 1's turn during an alert-level 3/4 conversation.

    The model keeps accompanying the person with the real conversation
    history, under a tightened instruction (short, present-focused, one
    concrete grounding offer, no resources of its own). The caller always
    appends the server-owned safety copy afterwards, so this returning
    None -- on a provider error, a refusal, or an empty reply -- costs the
    user nothing but the accompanying sentences.
    """
    try:
        provider = get_llm_provider(db)
        result = provider.chat(
            AGENT1_SYSTEM_PROMPT + "\n\n" + context_block + AGENT1_CRISIS_INSTRUCTION,
            _recent_messages(db, user_id),
            max_tokens=400,
        )
        return result if result.text.strip() else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent1 crisis accompaniment failed; using fixed safety copy only: %s", type(exc).__name__)
        return None


def _psychosocial_context_block(db: Session, user_id) -> str:
    """Give Agent 1 the person's situation, not just their scores.

    Knowing that someone moved out last week, lost a benefit, or stopped
    going to their group is what lets the reply land as "te acuerdas de lo
    del piso" instead of a generic check-in. It is read-only context: Agent 1
    still cannot compute risk or mention levels.
    """
    try:
        state = psychosocial.assess(db, user_id)
    except Exception:  # noqa: BLE001
        return ""
    if not state.domains:
        return ""

    lines = [
        f"- {item.label}: {item.category_label} ({item.valence})"
        for item in state.domains[:6]
    ]
    acute = [
        f"{item.category_label} ({item.observed_at:%d/%m})" for item in state.acute_changes[:3]
    ]
    block = (
        "Contexto psicosocial que la persona te ha contado (no lo cites como "
        "un registro del sistema; recuérdalo con naturalidad, como parte de lo "
        "que te ha ido contando, y solo si viene a cuento):\n" + "\n".join(lines) + "\n"
    )
    if acute:
        block += "Cambios recientes que puede estar atravesando: " + ", ".join(acute) + "\n"
    return block


def get_reply(db: Session, user: User, user_message: str) -> dict:
    # 1. Persist the user's message.
    correlation_id = uuid.uuid4()
    source_message = ChatMessage(user_id=user.id, role="user", content=user_message)
    db.add(source_message)
    db.commit()
    db.refresh(source_message)

    # 2. Analyse the free text: linguistic markers and social determinants
    #    in one call, both stored BEFORE the risk engine runs, so a sentence
    #    like "me he ido unos días a casa de un colega" is already on the
    #    record when the level is decided. Failures never reach the patient.
    analysis = analyze_text_and_store(
        db,
        user.id,
        user_message,
        source_type="chat_message",
        source_id=source_message.id,
        correlation_id=correlation_id,
        observed_at=source_message.created_at,
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
        + _psychosocial_context_block(db, user.id)
    )

    # Set by whichever branch actually reached a model, so the stored turn
    # records what answered rather than what the resolver would say now.
    reply_metadata: ProviderMetadata | None = None

    if level == 4:
        accompaniment = _agent1_crisis_accompaniment(db, user.id, context_block)
        prose = accompaniment.text.strip() if accompaniment else ""
        reply_text = (prose + "\n\n" if prose else "") + LEVEL4_PATIENT_MESSAGE
        ui_mode = "crisis"
        resources = CRISIS_RESOURCES
        reply_from_model = accompaniment is not None
        reply_metadata = accompaniment.metadata if accompaniment else None
    elif level == 3:
        has_prof = _has_active_professional(db, user.id)
        fixed = LEVEL3_PATIENT_MESSAGE_WITH_PROFESSIONAL if has_prof else LEVEL3_PATIENT_MESSAGE
        accompaniment = _agent1_crisis_accompaniment(db, user.id, context_block)
        prose = accompaniment.text.strip() if accompaniment else ""
        reply_text = (prose + "\n\n" if prose else "") + fixed
        ui_mode = "support"
        resources = None
        reply_from_model = accompaniment is not None
        reply_metadata = accompaniment.metadata if accompaniment else None
    else:
        # Levels 0-2: normal, open conversation via Agent 1, with the risk
        # context injected as READ-ONLY structured context (never the raw
        # number, per doc 8: "Nunca presentes esa información como un
        # diagnóstico").
        messages = _recent_messages(db, user.id)
        failed = False
        try:
            provider = get_llm_provider(db)
            result = provider.chat(AGENT1_SYSTEM_PROMPT + "\n\n" + context_block, messages)
            reply_metadata = result.metadata
            reply_text = result.text
        except Exception as exc:  # noqa: BLE001
            failed = True
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
        reply_from_model = not failed

    db.add(
        ChatMessage(
            user_id=user.id,
            role="assistant",
            content=reply_text,
            ui_mode=ui_mode,
            **_reply_provenance(db, from_model=reply_from_model, metadata=reply_metadata),
        )
    )
    db.commit()

    return {
        "reply": reply_text,
        "ui_mode": ui_mode,
        "resources": resources,
        "correlation_id": correlation_id,
    }
