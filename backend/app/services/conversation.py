"""
Conversation orchestration: wires Agent 2 (linguistic analysis) -> the
deterministic Risk Engine -> Agent 1 (conversational reply) together,
per the "flujo de respuesta" in doc 20.

Critical safety property, implemented here rather than left to prompting
alone: for alert_level 4 (and, with sharing, level 3) the message the
patient sees is built from the server-owned static templates in
app/content/safety_resources.py FIRST. The LLM is only ever allowed to
prepend a short, separately-generated empathetic sentence -- and if that
LLM call fails or times out for any reason, the hardcoded safety message
is still returned in full. The crisis path never depends on the LLM
succeeding.
"""
import logging

from sqlalchemy.orm import Session

from app.content.prompts import AGENT1_SYSTEM_PROMPT, AGENT2_SYSTEM_PROMPT, AGENT2_TOOL_SCHEMA
from app.content.safety_resources import (
    CRISIS_RESOURCES,
    LEVEL3_PATIENT_MESSAGE,
    LEVEL3_PATIENT_MESSAGE_WITH_PROFESSIONAL,
    LEVEL4_PATIENT_MESSAGE,
    LEVEL4_PATIENT_MESSAGE_SECONDARY,
)
from app.models import AlfaSignal, ChatMessage, PatientProfessionalAssignment, User
from app.services import risk_engine
from app.services.llm import get_llm_provider

logger = logging.getLogger("psychapp.conversation")

MAX_HISTORY_MESSAGES = 12


def analyze_text_and_store(db: Session, user_id, text: str) -> dict | None:
    """Call Agent 2 on free text and persist the result as an inference.
    Never raises: analysis failures degrade gracefully (no signal recorded,
    the risk engine simply proceeds without a fresh linguistic signal)."""
    try:
        provider = get_llm_provider()
        result = provider.analyze_structured(AGENT2_SYSTEM_PROMPT, text, AGENT2_TOOL_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent2 linguistic analysis failed: %s", exc)
        return None

    signal = AlfaSignal(
        user_id=user_id,
        signal_type="linguistic_analysis",
        value=result,
        confidence_band=None,
    )
    db.add(signal)
    db.commit()
    return result


def _has_active_professional(db: Session, user_id) -> bool:
    return (
        db.query(PatientProfessionalAssignment)
        .filter(PatientProfessionalAssignment.patient_id == user_id, PatientProfessionalAssignment.status == "active")
        .count()
        > 0
    )


def _agent1_wrapper_sentence(context: str) -> str | None:
    """A short, constrained call used only to add a warm sentence around
    a hardcoded safety message. Returns None on any failure (caller must
    then just use the hardcoded message alone)."""
    try:
        provider = get_llm_provider()
        constrained_prompt = (
            AGENT1_SYSTEM_PROMPT
            + "\n\nINSTRUCCIÓN ADICIONAL PARA ESTE TURNO: El sistema ya ha decidido mostrar "
            "un mensaje fijo de seguridad al usuario. Tu única tarea es devolver UNA frase "
            "breve (máx. 20 palabras), cálida, no alarmista, que valide el sufrimiento del "
            "usuario sin repetir números de teléfono ni inventar recursos. No añadas nada más."
        )
        reply = provider.chat(constrained_prompt, [{"role": "user", "content": context}], max_tokens=100)
        return reply.strip() or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Agent1 crisis wrapper sentence failed, using fallback copy only: %s", exc)
        return None


def get_reply(db: Session, user: User, user_message: str) -> dict:
    # 1. Persist the user's message.
    db.add(ChatMessage(user_id=user.id, role="user", content=user_message))
    db.commit()

    # 2. Agent 2: analyze the free text and store as an inference signal.
    analyze_text_and_store(db, user.id, user_message)

    # 3. Deterministic risk engine: the ONLY place alert_level is decided.
    assessment = risk_engine.run_and_persist(db, user.id)
    level = assessment.alert_level

    # 4. Build the reply. Levels 3/4 use server-owned copy; the LLM may
    #    only ever add a short wrapper sentence around it.
    if level == 4:
        wrapper = _agent1_wrapper_sentence(
            "El usuario puede estar en una crisis grave. Escribió: " + user_message[:500]
        )
        reply_text = (wrapper + "\n\n" if wrapper else "") + LEVEL4_PATIENT_MESSAGE
        ui_mode = "crisis"
        resources = CRISIS_RESOURCES
    elif level == 3:
        has_prof = _has_active_professional(db, user.id)
        reply_text = LEVEL3_PATIENT_MESSAGE_WITH_PROFESSIONAL if has_prof else LEVEL3_PATIENT_MESSAGE
        ui_mode = "support"
        resources = None
    else:
        # Levels 0-2: normal, open conversation via Agent 1, with the risk
        # context injected as READ-ONLY structured context (never the raw
        # number, per doc 8: "Nunca presentes esa información como un
        # diagnóstico").
        context_block = (
            f"[Contexto interno de solo lectura -- no lo reveles literalmente al usuario]\n"
            f"Motivo del estado actual: {assessment.assessment_reason}\n"
            f"Señales recientes: {assessment.input_signals}\n"
        )
        history = (
            db.query(ChatMessage)
            .filter(ChatMessage.user_id == user.id)
            .order_by(ChatMessage.created_at.desc())
            .limit(MAX_HISTORY_MESSAGES)
            .all()
        )
        history = list(reversed(history))
        messages = [{"role": m.role, "content": m.content} for m in history if m.role in ("user", "assistant")]
        try:
            provider = get_llm_provider()
            reply_text = provider.chat(AGENT1_SYSTEM_PROMPT + "\n\n" + context_block, messages)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Agent1 chat failed: %s", exc)
            err = str(exc).lower()
            if "authentication" in err or "invalid x-api-key" in err or "401" in err:
                reply_text = (
                    "El chat con Claude no está disponible: la ANTHROPIC_API_KEY no es válida. "
                    "En console.anthropic.com crea una API key (formato sk-ant-api…, no un token OAuth oat) "
                    "y ponla en el archivo .env del proyecto; luego reinicia con docker compose up -d. "
                    "Tus datos y check-ins se han guardado con normalidad."
                )
            elif "not configured" in err or "api_key" in err:
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

    return {"reply": reply_text, "ui_mode": ui_mode, "resources": resources}
