"""
Deterministic Risk Engine.

Direct implementation of the pseudocode in doc 17 ("Motor de Riesgo
Determinista") and the helper functions in doc 18. This module is the
single source of truth for alert_level (0-4). No LLM call ever happens
inside this module.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import AlfaSignal, ConfirmedFact, ProfessionalAlert, RiskAssessment
from app.services import baseline as baseline_service
from app.services import notifications as notification_service

MODEL_VERSION = "risk-engine-v1.1"

# N4 (emergencia): only explicit self-harm crisis declarations / ideation.
N4_FACT_CATEGORIES = {"ideation_active", "planning"}
# N3 (alarma profesional): consumption crisis alone is professional review, not 112.
N3_FACT_CATEGORIES = {"consumption_crisis"}
CRITICAL_DECLARATION_WINDOW_HOURS = 48
STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT = 3
STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE = 5
# Do not spam professionals with duplicate open alerts at the same level.
ALERT_DEDUPE_HOURS = 24


@dataclass
class RiskDecision:
    level: int
    triggering_rules: list[str]
    reason: str
    input_signals: dict
    input_facts: dict


def _facts_in_categories(db: Session, user_id, categories: set[str], window_hours: int) -> list[dict]:
    since = datetime.utcnow() - timedelta(hours=window_hours)
    facts = (
        db.query(ConfirmedFact)
        .filter(
            ConfirmedFact.user_id == user_id,
            ConfirmedFact.is_active == True,  # noqa: E712
            ConfirmedFact.category.in_(categories),
            ConfirmedFact.created_at >= since,
        )
        .all()
    )
    return [{"category": f.category, "content": f.content, "created_at": f.created_at.isoformat()} for f in facts]


def _latest_linguistic_signal(db: Session, user_id) -> dict:
    sig = (
        db.query(AlfaSignal)
        .filter(AlfaSignal.user_id == user_id, AlfaSignal.signal_type == "linguistic_analysis")
        .order_by(AlfaSignal.timestamp.desc())
        .first()
    )
    return (sig.value if sig else {}) or {}


def _linguistic_flags(db: Session, user_id, window_hours: int = 12) -> dict:
    """
    Only *recent* Agent-2 linguistic analyses count toward live risk.
    A short window avoids a single diary/chat turn permanently locking the
    patient at N4 until the next analysis overwrites it days later.
    """
    since = datetime.utcnow() - timedelta(hours=window_hours)
    signals = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.user_id == user_id,
            AlfaSignal.signal_type == "linguistic_analysis",
            AlfaSignal.is_active == True,  # noqa: E712
            AlfaSignal.timestamp >= since,
        )
        .order_by(AlfaSignal.timestamp.desc())
        .first()
    )
    value = (signals.value if signals else {}) or {}
    # Agent 2 sometimes returns strings "true"/"false"; coerce carefully.
    def _truthy(v) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return v != 0
        if isinstance(v, str):
            return v.strip().lower() in {"true", "1", "yes", "si", "sí"}
        return False

    return {
        "ideation_direct": _truthy(value.get("ideation_direct")),
        "consumption_crisis": _truthy(value.get("consumption_crisis")),
        "rumination_score": value.get("rumination_score"),
        "raw": value,
    }


def _persistence_band(db: Session, user_id, band: str, days_minimum: int) -> bool:
    """
    Require at least `days_minimum` distinct calendar days with structural
    scores in the given band (not merely N rows in one day).
    """
    since = datetime.utcnow() - timedelta(days=days_minimum)
    history = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.user_id == user_id,
            AlfaSignal.signal_type == "structural_score",
            AlfaSignal.is_active == True,  # noqa: E712
            AlfaSignal.timestamp >= since,
        )
        .order_by(AlfaSignal.timestamp.desc())
        .all()
    )
    days_in_band = {h.timestamp.strftime("%Y-%m-%d") for h in history if h.confidence_band == band}
    return len(days_in_band) >= days_minimum


def _convergencia_critica_extrema(structural_score: float | None, rumination: float | None, sleep_worsening: bool) -> bool:
    if structural_score is None or rumination is None:
        return False
    return structural_score < 0.20 and rumination > 0.85 and sleep_worsening


def calculate_risk_level(db: Session, user_id) -> RiskDecision:
    structural = baseline_service.compute_structural_score(db, user_id)
    ling_flags = _linguistic_flags(db, user_id)
    linguistic = ling_flags.get("raw") or _latest_linguistic_signal(db, user_id)
    rumination = ling_flags.get("rumination_score")
    if rumination is None:
        rumination = linguistic.get("rumination_score")

    from app.models import CheckIn

    recent_checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id)
        .order_by(CheckIn.created_at.desc())
        .limit(7)
        .all()
    )
    sleep_values = [c.sleep_hours for c in reversed(recent_checkins)]
    sleep_trend = baseline_service.calculate_trend(db, user_id, sleep_values)
    sleep_worsening = sleep_trend == "empeorando"
    rumination_trend = "aumentando" if (rumination or 0) > 0.6 else "estable"

    n4_facts = _facts_in_categories(db, user_id, N4_FACT_CATEGORIES, CRITICAL_DECLARATION_WINDOW_HOURS)
    n3_facts = _facts_in_categories(db, user_id, N3_FACT_CATEGORIES, CRITICAL_DECLARATION_WINDOW_HOURS)

    input_signals = {
        "structural_score": structural.score,
        "confidence_band": structural.confidence_band,
        "z_scores": structural.z_scores,
        "linguistic": linguistic,
        "linguistic_flags": {
            "ideation_direct": ling_flags["ideation_direct"],
            "consumption_crisis": ling_flags["consumption_crisis"],
        },
        "sleep_trend": sleep_trend,
    }
    input_facts = {
        "n4_declarations": n4_facts,
        "n3_declarations": n3_facts,
        # backward-compatible key used in older UI
        "critical_declarations": n4_facts + n3_facts,
    }

    triggering_rules: list[str] = []

    # ---------------- Nivel 4 (Emergencia) ----------------
    # Only true emergency declarations / direct ideation / extreme multi-signal convergence.
    if n4_facts:
        triggering_rules.append("N4_declaracion_ideacion_o_plan")
        return RiskDecision(
            level=4,
            triggering_rules=triggering_rules,
            reason="Declaración confirmada de ideación activa o planificación (hecho, no inferencia)",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    if ling_flags["ideation_direct"]:
        triggering_rules.append("N4_senal_linguistica_ideacion_directa")
        return RiskDecision(
            level=4,
            triggering_rules=triggering_rules,
            reason="Señal lingüística reciente de ideación directa (inferencia Agent 2; revisión humana prioritaria)",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    if _convergencia_critica_extrema(structural.score, rumination if isinstance(rumination, (int, float)) else None, sleep_worsening):
        triggering_rules.append("N4_convergencia_critica_extrema")
        return RiskDecision(
            level=4,
            triggering_rules=triggering_rules,
            reason="Convergencia extrema: score estructural muy bajo + rumiación alta + sueño empeorando",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    # ---------------- Nivel 3 (Alarma profesional) ----------------
    if n3_facts:
        triggering_rules.append("N3_declaracion_crisis_consumo")
        return RiskDecision(
            level=3,
            triggering_rules=triggering_rules,
            reason="Declaración de crisis de consumo (alarma profesional, no emergencia 112 automática)",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    if ling_flags["consumption_crisis"]:
        triggering_rules.append("N3_senal_linguistica_crisis_consumo")
        return RiskDecision(
            level=3,
            triggering_rules=triggering_rules,
            reason="Señal lingüística de crisis de consumo (inferencia; revisión profesional)",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    if structural.confidence_band == "unstable" and _persistence_band(
        db, user_id, "unstable", STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT
    ) and (sleep_worsening or rumination_trend == "aumentando"):
        triggering_rules.append("N3_unstable_persistente_con_convergencia")
        return RiskDecision(
            level=3,
            triggering_rules=triggering_rules,
            reason="Desviación estructural persistente (≥3 días inestable) con convergencia de señales",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    if structural.confidence_band == "unstable" and _persistence_band(
        db, user_id, "unstable", STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE
    ):
        triggering_rules.append("N3_unstable_persistente")
        return RiskDecision(
            level=3,
            triggering_rules=triggering_rules,
            reason="structural_score en banda inestable de forma sostenida (≥5 días)",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    # ---------------- Nivel 2 (Prevención) ----------------
    if structural.confidence_band == "transition" or (
        structural.confidence_band == "unstable" and _persistence_band(db, user_id, "unstable", 1)
    ):
        triggering_rules.append("N2_desviacion_moderada")
        return RiskDecision(
            level=2,
            triggering_rules=triggering_rules,
            reason="Desviación moderada o inicio de inestabilidad (prevención, sin alerta profesional automática)",
            input_signals=input_signals,
            input_facts=input_facts,
        )

    # ---------------- Nivel 0-1 (Autogestión) ----------------
    if structural.confidence_band == "stable" or structural.confidence_band == "insufficient_data":
        level = 0 if structural.confidence_band == "stable" else 1
        rule = "N0_estable" if level == 0 else "N1_datos_insuficientes_o_sin_criterios"
        reason = (
            "Situación estable respecto a la línea base personal"
            if level == 0
            else "Sin criterios de nivel superior (datos insuficientes o sin desviación clara)"
        )
        triggering_rules.append(rule)
        return RiskDecision(
            level=level,
            triggering_rules=triggering_rules,
            reason=reason,
            input_signals=input_signals,
            input_facts=input_facts,
        )

    triggering_rules.append("N1_sin_criterios_superiores")
    return RiskDecision(
        level=1,
        triggering_rules=triggering_rules,
        reason="Situación dentro de parámetros de autogestión",
        input_signals=input_signals,
        input_facts=input_facts,
    )


def _find_open_alert(db: Session, user_id, level: int) -> ProfessionalAlert | None:
    since = datetime.utcnow() - timedelta(hours=ALERT_DEDUPE_HOURS)
    return (
        db.query(ProfessionalAlert)
        .filter(
            ProfessionalAlert.user_id == user_id,
            ProfessionalAlert.alert_level == level,
            ProfessionalAlert.status.in_(["open", "acknowledged"]),
            ProfessionalAlert.created_at >= since,
            ProfessionalAlert.source == "rule_engine",
        )
        .order_by(ProfessionalAlert.created_at.desc())
        .first()
    )


def _highest_open_level(db: Session, user_id) -> int:
    row = (
        db.query(ProfessionalAlert)
        .filter(
            ProfessionalAlert.user_id == user_id,
            ProfessionalAlert.status.in_(["open", "acknowledged"]),
        )
        .order_by(ProfessionalAlert.alert_level.desc())
        .first()
    )
    return row.alert_level if row else 0


def run_and_persist(db: Session, user_id) -> RiskAssessment:
    """
    Full pipeline: evaluate -> persist assessment -> create professional_alerts
    if level >= 3 (deduped) -> enqueue notifications.
    """
    decision = calculate_risk_level(db, user_id)

    assessment = RiskAssessment(
        user_id=user_id,
        alert_level=decision.level,
        triggering_rules=decision.triggering_rules,
        input_signals=decision.input_signals,
        input_facts=decision.input_facts,
        confidence=decision.input_signals.get("structural_score"),
        assessment_reason=decision.reason,
        model_version=MODEL_VERSION,
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)

    if decision.level >= 3:
        existing = _find_open_alert(db, user_id, decision.level)
        if existing:
            # Refresh the open alert instead of mis-classifying duplicates.
            existing.description = decision.reason
            existing.related_signals = {
                **(decision.input_signals or {}),
                "triggering_rules": decision.triggering_rules,
                "assessment_id": str(assessment.id),
            }
            existing.related_assessment_id = assessment.id
            existing.title = _alert_title(decision)
            db.commit()
            assessment.generated_alert_id = existing.id
            db.commit()
        elif decision.level > _highest_open_level(db, user_id):
            alert = ProfessionalAlert(
                user_id=user_id,
                alert_level=decision.level,
                status="open",
                source="rule_engine",
                title=_alert_title(decision),
                description=_alert_description(decision),
                related_signals={
                    **(decision.input_signals or {}),
                    "triggering_rules": decision.triggering_rules,
                },
                related_assessment_id=assessment.id,
            )
            db.add(alert)
            db.commit()
            db.refresh(alert)

            assessment.generated_alert_id = alert.id
            db.commit()

            notification_service.dispatch_for_alert(db, alert)

    return assessment


def _alert_title(decision: RiskDecision) -> str:
    if decision.level == 4:
        return "ALERTA NIVEL 4 – EMERGENCIA"
    if decision.level == 3:
        if "N3_declaracion_crisis_consumo" in decision.triggering_rules:
            return "Alerta Nivel 3 – Crisis de consumo declarada"
        if "N3_senal_linguistica_crisis_consumo" in decision.triggering_rules:
            return "Alerta Nivel 3 – Señal lingüística de crisis de consumo"
        if "N3_unstable_persistente_con_convergencia" in decision.triggering_rules:
            return "Alerta Nivel 3 – Desviación estructural con convergencia"
        return "Alerta Nivel 3 – Desviación estructural persistente"
    if decision.level == 2:
        return "Nivel 2 – Prevención (sin alerta profesional automática)"
    return "Estado de autogestión"


def _alert_description(decision: RiskDecision) -> str:
    rules = ", ".join(decision.triggering_rules) if decision.triggering_rules else "—"
    return (
        f"{decision.reason}\n"
        f"Reglas disparadas: {rules}\n"
        f"Banda estructural: {decision.input_signals.get('confidence_band')} · "
        f"score={decision.input_signals.get('structural_score')}"
    )
