"""
Agent 3 — the therapist's clinical copilot.

A therapist selects one of their patients and talks to the model about
that patient: "resúmeme la situación", "¿de qué ha hablado esta semana?",
"¿por qué saltó la alerta del martes?".

Design constraints, all deliberate:

  * Read-only. This service never writes a ConfirmedFact, an AlfaSignal, a
    RiskAssessment or a ProfessionalAlert. Agent 3 cannot move a patient's
    alert level, so a hallucination here can mislead a professional but can
    never change what the deterministic engine decided or what the patient
    is shown.
  * Both sources. The dossier handed to the model contains the patient's
    diary AND their chat with Agent 1, because both are what the patient
    actually "told" the system, and Agent 2 analyses both.
  * Attributed. Every item in the dossier is stamped with its origin and
    date, and the system prompt requires the model to cite them, so the
    therapist can verify any statement against the corresponding tab.
  * Bounded. The dossier is capped per section so one very talkative
    patient cannot push the request past the model's context window.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.prompts import (
    AGENT3_PROMPT_VERSION,
    AGENT3_SUMMARY_REQUEST,
    AGENT3_SYSTEM_PROMPT,
)
from app.models import (
    AlfaSignal,
    ChatMessage,
    CheckIn,
    ConfirmedFact,
    DiaryEntry,
    ProfessionalAlert,
    RiskAssessment,
    SafetyPlan,
    TherapistCopilotMessage,
    User,
)
from app.services import clinical_view, psychosocial
from app.services import llm_config
from app.services.llm import build_provider

logger = logging.getLogger("psychapp.copilot")

DEFAULT_WINDOW_DAYS = 60
MAX_CHECKINS = 45
MAX_DIARY = 25
MAX_CHAT = 60
MAX_FACTS = 30
MAX_ASSESSMENTS = 15
MAX_ALERTS = 15
MAX_SIGNALS = 25
MAX_HISTORY_TURNS = 16
DIARY_CHARS = 1200
CHAT_CHARS = 700
MAX_ANSWER_TOKENS = 2000


@dataclass(frozen=True)
class CopilotReply:
    content: str
    context_counts: dict[str, int]
    error_kind: str | None


def _fmt(value: datetime | None) -> str:
    return value.strftime("%d/%m/%Y %H:%M") if value else "sin fecha"


def _clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_dossier_text(db: Session, patient: User, window_days: int = DEFAULT_WINDOW_DAYS) -> tuple[str, dict[str, int]]:
    """Render the patient's record as attributed plain text for the model."""
    since = datetime.utcnow() - timedelta(days=window_days)
    parts: list[str] = []
    counts: dict[str, int] = {}

    parts.append(
        f"# EXPEDIENTE DE {patient.display_name}\n"
        f"Ventana: últimos {window_days} días. Fecha de hoy: {_fmt(datetime.utcnow())}.\n"
        f"Todas las fechas están en formato dd/mm/aaaa."
    )

    # --- current deterministic state --------------------------------------
    assessment = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient.id)
        .order_by(RiskAssessment.calculated_at.desc())
        .first()
    )
    explanation = clinical_view.level_explanation(assessment)
    structural = clinical_view.structural_explanation(assessment)
    parts.append(
        "## ESTADO ACTUAL SEGÚN EL MOTOR DETERMINISTA (no es tuyo, no lo recalcules)\n"
        f"- Nivel: {explanation['level_label']}\n"
        f"- Regla que lo disparó: {explanation['rule_code']} — {explanation['rule_title']}\n"
        f"- Tipo de evidencia: {explanation['driver_family_label']}\n"
        f"- Calculado: {explanation['calculated_at'] or 'nunca'}\n"
        f"- Score estructural: {structural['summary']}\n"
        f"- Lectura direccional: {structural['direction_summary'] or '—'}"
    )

    # --- check-ins --------------------------------------------------------
    checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == patient.id, CheckIn.created_at >= since)
        .order_by(CheckIn.created_at.desc())
        .limit(MAX_CHECKINS)
        .all()
    )
    counts["checkins"] = len(checkins)
    if checkins:
        rows = "\n".join(
            f"- {_fmt(c.created_at)} · ánimo {c.mood}/10 · craving {c.craving}/10 · "
            f"sueño {c.sleep_hours} h · autoeficacia {c.self_efficacy}/10"
            + (f" · nota: {_clip(c.notes, 200)}" if c.notes else "")
            for c in reversed(checkins)
        )
        parts.append(f"## CHECK-INS DIARIOS (dato declarado por el paciente)\n{rows}")
    else:
        parts.append("## CHECK-INS DIARIOS\nNo hay check-ins en la ventana.")

    # --- diary ------------------------------------------------------------
    diary = (
        db.query(DiaryEntry)
        .filter(DiaryEntry.user_id == patient.id, DiaryEntry.created_at >= since)
        .order_by(DiaryEntry.created_at.desc())
        .limit(MAX_DIARY)
        .all()
    )
    counts["diary"] = len(diary)
    if diary:
        rows = "\n".join(f"- [{_fmt(d.created_at)}] {_clip(d.content, DIARY_CHARS)}" for d in reversed(diary))
        parts.append(f"## DIARIO (texto literal del paciente)\n{rows}")
    else:
        parts.append("## DIARIO\nNo hay entradas de diario en la ventana.")

    # --- chat with Agent 1 ------------------------------------------------
    chat = (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == patient.id, ChatMessage.created_at >= since)
        .order_by(ChatMessage.created_at.desc())
        .limit(MAX_CHAT)
        .all()
    )
    counts["chat"] = len(chat)
    if chat:
        rows = "\n".join(
            f"- [{_fmt(m.created_at)}] {'PACIENTE' if m.role == 'user' else 'ASISTENTE'}: "
            f"{_clip(m.content, CHAT_CHARS)}"
            for m in reversed(chat)
        )
        parts.append(
            "## CHAT DEL PACIENTE CON EL ASISTENTE (Agente 1)\n"
            "Lo escrito por PACIENTE es texto literal suyo. Lo escrito por ASISTENTE es del sistema.\n"
            f"{rows}"
        )
    else:
        parts.append("## CHAT DEL PACIENTE\nNo hay mensajes de chat en la ventana.")

    # --- confirmed facts --------------------------------------------------
    facts = (
        db.query(ConfirmedFact)
        .filter(ConfirmedFact.user_id == patient.id, ConfirmedFact.is_active == True)  # noqa: E712
        .order_by(ConfirmedFact.created_at.desc())
        .limit(MAX_FACTS)
        .all()
    )
    counts["facts"] = len(facts)
    if facts:
        rows = "\n".join(
            f"- [{_fmt(f.created_at)}] {f.category} (declarado por {f.declared_by}): {_clip(f.content, 400)}"
            for f in reversed(facts)
        )
        parts.append(f"## HECHOS CONFIRMADOS (HECHOS, no inferencias; el sistema no puede sobrescribirlos)\n{rows}")
    else:
        parts.append("## HECHOS CONFIRMADOS\nNinguno activo.")

    # --- psychosocial context ---------------------------------------------
    assessment_state = psychosocial.assess(db, patient.id)
    counts["psychosocial_domains"] = assessment_state.active_count
    if assessment_state.domains:
        rows = []
        for state in assessment_state.domains:
            marks = []
            if state.is_change:
                marks.append("CAMBIO RECIENTE")
            if state.status != "inferred":
                marks.append(state.status.upper())
            rows.append(
                f"- {state.label} · {state.category_label} · {state.valence} "
                f"(intensidad {state.intensity}, confianza {state.confidence})"
                + (f" [{', '.join(marks)}]" if marks else "")
                + f" — {_clip(state.summary, 240)}"
                + (f" · cita literal: «{_clip(state.quote, 200)}»" if state.quote else "")
                + f" · {_fmt(state.observed_at)}"
            )
        acute = ""
        if assessment_state.acute_changes:
            acute = "\nCAMBIOS ADVERSOS EN LOS ÚLTIMOS 14 DÍAS: " + ", ".join(
                f"{state.category_label} ({_fmt(state.observed_at)})"
                for state in assessment_state.acute_changes
            )
        parts.append(
            "## CONTEXTO PSICOSOCIAL (INFERENCIAS del Agente 4 sobre lo que el paciente contó)\n"
            f"Índice de vulnerabilidad: {assessment_state.index} ({assessment_state.band}). "
            "Más alto es peor. No es un instrumento validado.\n"
            + "\n".join(rows)
            + acute
        )
    else:
        parts.append(
            "## CONTEXTO PSICOSOCIAL\n"
            "Sin observaciones. El paciente no ha hablado de su vivienda, apoyo, familia, dinero ni "
            "situación vital, o el extractor no encontró nada. Es un hueco relevante: puedes sugerir al "
            "profesional que lo explore."
        )

    # --- Agent 2 signals --------------------------------------------------
    signals = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.user_id == patient.id,
            AlfaSignal.signal_type == "linguistic_analysis",
            AlfaSignal.timestamp >= since,
        )
        .order_by(AlfaSignal.timestamp.desc())
        .limit(MAX_SIGNALS)
        .all()
    )
    counts["linguistic_signals"] = len(signals)
    if signals:
        rows = []
        for s in reversed(signals):
            value = s.value if isinstance(s.value, dict) else {}
            flags = [
                name
                for name, key in (
                    ("ideación directa", "ideation_direct"),
                    ("ideación indirecta", "ideation_indirect"),
                    ("crisis de consumo", "consumption_crisis"),
                )
                if value.get(key)
            ]
            rows.append(
                f"- [{_fmt(s.timestamp)}] rumiación {value.get('rumination_score')} · "
                f"valencia negativa {value.get('negative_valence')} · urgencia {value.get('urgency_level')} · "
                f"ambivalencia {value.get('ambivalence')}"
                + (f" · BANDERAS: {', '.join(flags)}" if flags else "")
                + (f" · «{_clip(value.get('short_rationale'), 200)}»" if value.get("short_rationale") else "")
            )
        parts.append(
            "## SEÑALES DEL AGENTE 2 (INFERENCIAS de un modelo de lenguaje sobre los textos anteriores)\n"
            + "\n".join(rows)
        )
    else:
        parts.append("## SEÑALES DEL AGENTE 2\nNinguna en la ventana.")

    # --- assessments and alerts -------------------------------------------
    assessments = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient.id, RiskAssessment.calculated_at >= since)
        .order_by(RiskAssessment.calculated_at.desc())
        .limit(MAX_ASSESSMENTS)
        .all()
    )
    counts["assessments"] = len(assessments)
    if assessments:
        rows = "\n".join(
            f"- [{_fmt(a.calculated_at)}] nivel {a.alert_level} · regla "
            f"{clinical_view.selected_rule_code(a)} · {_clip(a.assessment_reason, 220)}"
            for a in reversed(assessments)
        )
        parts.append(f"## EVALUACIONES DEL MOTOR DETERMINISTA\n{rows}")

    alerts = (
        db.query(ProfessionalAlert)
        .filter(ProfessionalAlert.user_id == patient.id, ProfessionalAlert.created_at >= since)
        .order_by(ProfessionalAlert.created_at.desc())
        .limit(MAX_ALERTS)
        .all()
    )
    counts["alerts"] = len(alerts)
    if alerts:
        rows = "\n".join(
            f"- [{_fmt(a.created_at)}] nivel {a.alert_level} · {a.status} · {a.title}"
            + (f" · resolución: {_clip(a.resolution_notes, 200)}" if a.resolution_notes else "")
            + (f" · descartada por: {_clip(a.dismiss_reason, 200)}" if a.dismiss_reason else "")
            for a in reversed(alerts)
        )
        parts.append(f"## ALERTAS PROFESIONALES\n{rows}")

    plan = db.query(SafetyPlan).filter(SafetyPlan.user_id == patient.id).first()
    if plan:
        parts.append(
            "## PLAN DE SEGURIDAD (escrito por el paciente)\n"
            f"- Señales de alarma: {_clip(plan.warning_signs, 300) or '—'}\n"
            f"- Afrontamiento: {_clip(plan.coping_strategies, 300) or '—'}\n"
            f"- Apoyos: {_clip(plan.social_supports, 300) or '—'}\n"
            f"- Contactos profesionales: {_clip(plan.professional_contacts, 300) or '—'}\n"
            f"- Entorno seguro: {_clip(plan.safe_environment, 300) or '—'}\n"
            f"- Razones para vivir: {_clip(plan.reasons_to_live, 300) or '—'}"
        )
        counts["safety_plan"] = 1
    else:
        parts.append("## PLAN DE SEGURIDAD\nEl paciente no ha guardado ninguno.")
        counts["safety_plan"] = 0

    return "\n\n".join(parts), counts


def history(db: Session, professional_id, patient_id, limit: int = 200) -> list[TherapistCopilotMessage]:
    return (
        db.query(TherapistCopilotMessage)
        .filter(
            TherapistCopilotMessage.professional_id == professional_id,
            TherapistCopilotMessage.patient_id == patient_id,
        )
        .order_by(TherapistCopilotMessage.created_at.asc())
        .limit(limit)
        .all()
    )


def _recent_turns(db: Session, professional_id, patient_id) -> list[dict[str, str]]:
    rows = (
        db.query(TherapistCopilotMessage)
        .filter(
            TherapistCopilotMessage.professional_id == professional_id,
            TherapistCopilotMessage.patient_id == patient_id,
        )
        .order_by(TherapistCopilotMessage.created_at.desc())
        .limit(MAX_HISTORY_TURNS)
        .all()
    )
    return [{"role": row.role, "content": row.content} for row in reversed(rows) if row.role in ("user", "assistant")]


def ask(
    db: Session,
    *,
    professional: User,
    patient: User,
    question: str,
    kind: str = "question",
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> TherapistCopilotMessage:
    """Persist the professional's turn, answer it, persist the answer.

    A provider failure is stored as an assistant turn carrying the reason,
    so the therapist sees why the panel is silent instead of an empty box.
    """
    settings = get_settings()

    stored_question = TherapistCopilotMessage(
        id=uuid.uuid4(),
        professional_id=professional.id,
        patient_id=patient.id,
        role="user",
        content=question,
        kind="question",
        context_window_days=window_days,
    )
    db.add(stored_question)
    db.commit()

    dossier, counts = build_dossier_text(db, patient, window_days)
    system_prompt = (
        AGENT3_SYSTEM_PROMPT
        + "\n\n### EXPEDIENTE DEL PACIENTE SELECCIONADO\n"
        + dossier
        + "\n\n### FIN DEL EXPEDIENTE\n"
        + "Responde solo con lo que sostenga este expediente. Si algo no está aquí, dilo."
    )

    # Resolved once, so the row records the model that actually answered
    # rather than whatever the environment happens to say today.
    active = llm_config.resolve(db)

    error_kind: str | None = None
    try:
        content = build_provider(active).chat(
            system_prompt,
            _recent_turns(db, professional.id, patient.id),
            max_tokens=MAX_ANSWER_TOKENS,
        ).strip()
        if not content:
            raise RuntimeError("empty_reply")
    except Exception as exc:  # noqa: BLE001
        error_kind = type(exc).__name__[:64]
        logger.warning("Agent 3 copilot call failed: %s", error_kind)
        content = (
            "No he podido generar la respuesta ahora mismo "
            f"(error del proveedor: {error_kind}). "
            "El expediente del paciente sigue disponible en las pestañas de esta ficha: "
            "check-ins, diario, chat, hechos, evidencia y motor de riesgo. "
            + (
                "Si esto se repite, revisa que el servidor del modelo esté accesible desde el backend."
                if active.is_local
                else "Si esto se repite, revisa que ANTHROPIC_API_KEY esté configurada en el servidor."
            )
        )

    answer = TherapistCopilotMessage(
        id=uuid.uuid4(),
        professional_id=professional.id,
        patient_id=patient.id,
        role="assistant",
        content=content,
        kind="summary" if kind == "summary" else "answer",
        provider=active.provider,
        requested_model=active.chat_model,
        context_window_days=window_days,
        context_counts={**counts, "prompt_version": AGENT3_PROMPT_VERSION},
        error_kind=error_kind,
    )
    db.add(answer)
    db.commit()
    db.refresh(answer)
    return answer


def summarize(
    db: Session,
    *,
    professional: User,
    patient: User,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> TherapistCopilotMessage:
    return ask(
        db,
        professional=professional,
        patient=patient,
        question=AGENT3_SUMMARY_REQUEST,
        kind="summary",
        window_days=window_days,
    )
