"""
Deterministic Risk Engine.

Direct implementation of the pseudocode in doc 17 ("Motor de Riesgo
Determinista") and the helper functions in doc 18. This module is the
single source of truth for alert_level (0-4). No LLM call ever happens
inside this module.

Four families of evidence reach it, all of them precomputed elsewhere:

  * confirmed facts (declarations by the patient or a professional),
  * Agent 2's linguistic signals over one recent text,
  * the structural score over the patient's own check-in baseline,
  * and, since v1.3, the psychosocial profile built by
    ``app/services/psychosocial.py`` from what the patient told Agent 1
    about their housing, money, household and support network.

The psychosocial rules exist because of a specific blind spot. Losing your
flat, your sister moving out and giving your guitar away are three sentences
that trip no linguistic flag and touch no check-in, so the first three
families see nothing at all. Read together, against fixed thresholds, they
are the constellation that precedes a crisis. The engine reads them the same
way it reads everything else: ordered rules, explicit conditions, the whole
evaluation written into ``calculation_trace``.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.content.psychosocial_catalog import domain_label
from app.models import AlfaSignal, ConfirmedFact, ProfessionalAlert, RiskAssessment
from app.services import baseline as baseline_service
from app.services import notifications as notification_service
from app.services import psychosocial as psychosocial_service

MODEL_VERSION = "risk-engine-v1.3"

# N4 (emergencia): only explicit self-harm crisis declarations / ideation.
N4_FACT_CATEGORIES = {"ideation_active", "planning"}
# N3 (alarma profesional): consumption crisis alone is professional review, not 112.
N3_FACT_CATEGORIES = {"consumption_crisis"}
CRITICAL_DECLARATION_WINDOW_HOURS = 48
STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT = 3
STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE = 5
# Do not spam professionals with duplicate open alerts at the same level.
ALERT_DEDUPE_HOURS = 24

# Thresholds for the "subtle inner signal" half of the psychosocial rules.
# Deliberately below the levels that fire on their own: the point of these
# rules is that neither half would trigger anything by itself.
SUBTLE_RUMINATION_MIN = 0.60
SUBTLE_NEGATIVE_VALENCE_MIN = 0.70


def _utc_iso(value: datetime) -> str:
    """Serialize legacy naive database timestamps explicitly as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


@dataclass
class RiskDecision:
    level: int
    triggering_rules: list[str]
    reason: str
    input_signals: dict
    input_facts: dict
    calculation_trace: dict | None = None
    linguistic_signal_id: object | None = None


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
    # The persisted calculation snapshot needs the evidence identity and
    # category, not a second copy of the sensitive free-text fact.  Clinical
    # content remains in confirmed_facts under its existing RBAC rules.
    return [
        {"id": str(f.id), "category": f.category, "created_at": _utc_iso(f.created_at)}
        for f in facts
    ]


def _latest_linguistic_signal(db: Session, user_id) -> dict:
    sig = (
        db.query(AlfaSignal)
        .filter(AlfaSignal.user_id == user_id, AlfaSignal.signal_type == "linguistic_analysis")
        .order_by(AlfaSignal.timestamp.desc())
        .first()
    )
    return (sig.value if sig else {}) or {}


def _linguistic_flags(
    db: Session,
    user_id,
    window_hours: int = 12,
    *,
    signal_id=None,
) -> dict:
    """
    Only *recent* Agent-2 linguistic analyses count toward live risk.
    A short window avoids a single diary/chat turn permanently locking the
    patient at N4 until the next analysis overwrites it days later.
    """
    since = datetime.utcnow() - timedelta(hours=window_hours)
    query = db.query(AlfaSignal).filter(
        AlfaSignal.user_id == user_id,
        AlfaSignal.signal_type == "linguistic_analysis",
        AlfaSignal.is_active == True,  # noqa: E712
        AlfaSignal.timestamp >= since,
    )
    if signal_id is not None:
        # Chat/diary evaluations must consume the exact signal produced for
        # their own source text.  Selecting the global latest signal here
        # would let concurrent requests for one patient cross their lineage.
        query = query.filter(AlfaSignal.id == signal_id)
    signals = query.order_by(AlfaSignal.timestamp.desc()).first()
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
        "_signal_uuid": signals.id if signals else None,
        "signal_id": str(signals.id) if signals else None,
        "signal_timestamp": _utc_iso(signals.timestamp) if signals else None,
        "eligible_for_risk": bool(signals),
        "freshness_window_hours": window_hours,
        "ideation_direct": _truthy(value.get("ideation_direct")),
        # Indirect ideation raises nothing on its own -- it is far too common
        # to alert on -- but it is the inner half of the psychosocial
        # convergence rules below.
        "ideation_indirect": _truthy(value.get("ideation_indirect")),
        "consumption_crisis": _truthy(value.get("consumption_crisis")),
        "rumination_score": value.get("rumination_score"),
        "negative_valence": value.get("negative_valence"),
        "raw": value,
    }


def _psychosocial_profile(db: Session, user_id):
    """Current psychosocial picture, computed deterministically from rows.

    Wrapped in its own function so the rule table below reads uniformly and
    so tests can substitute a profile without a database.
    """
    return psychosocial_service.current_profile(db, user_id)


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


def _persistence_detail(db: Session, user_id, band: str, days_minimum: int) -> dict:
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
    days = sorted({row.timestamp.strftime("%Y-%m-%d") for row in history if row.confidence_band == band})
    return {
        "band": band,
        "window_days": days_minimum,
        "required_distinct_days": days_minimum,
        "observed_distinct_days": len(days),
        "observed_dates": days,
        "passed": len(days) >= days_minimum,
    }


def _convergencia_critica_extrema(structural_score: float | None, rumination: float | None, sleep_worsening: bool) -> bool:
    if structural_score is None or rumination is None:
        return False
    return structural_score < 0.20 and rumination > 0.85 and sleep_worsening


def _calculate_risk_level_legacy(db: Session, user_id) -> RiskDecision:
    """Pre-trace implementation retained temporarily for migration comparison tests."""
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


def _trace_condition(label: str, actual, operator: str, expected, passed: bool | None) -> dict:
    return {
        "label": label,
        "actual": actual,
        "operator": operator,
        "expected": expected,
        "result": passed,
    }


def _trace_rule(code: str, level: int, label: str, conditions: list[dict], matched: bool | None) -> dict:
    return {
        "priority": 0,
        "code": code,
        "target_level": level,
        "label": label,
        "conditions": conditions,
        "matched": matched,
        "selected": False,
        "status": "not_evaluable" if matched is None else ("matched_not_selected" if matched else "not_matched"),
    }


def calculate_risk_level(db: Session, user_id, *, linguistic_signal_id=None) -> RiskDecision:
    """Evaluate every deterministic rule and persistable intermediate.

    Unlike the legacy early-return cascade, this function records all rule
    outcomes and then selects the first match.  The ordering and final level
    remain identical, while clinicians can inspect the complete calculation.
    """

    evaluated_at = datetime.utcnow()
    structural = baseline_service.compute_structural_score(db, user_id)
    ling = _linguistic_flags(db, user_id, signal_id=linguistic_signal_id)
    linguistic = ling.get("raw") or {}
    rumination = ling.get("rumination_score")

    from app.models import CheckIn

    recent_checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id)
        .order_by(CheckIn.created_at.desc())
        .limit(7)
        .all()
    )
    ordered_checkins = list(reversed(recent_checkins))
    sleep_values = [float(row.sleep_hours) for row in ordered_checkins]
    sleep_detail = baseline_service.calculate_trend_detail(sleep_values)
    sleep_worsening = sleep_detail.label == "empeorando"
    craving_values = [float(row.craving) for row in ordered_checkins]
    craving_detail = baseline_service.calculate_trend_detail(craving_values)
    craving_rising = craving_detail.label == "aumentando"
    rumination_high = isinstance(rumination, (int, float)) and rumination > SUBTLE_RUMINATION_MIN
    negative_valence = ling.get("negative_valence")
    negative_valence_high = (
        isinstance(negative_valence, (int, float)) and negative_valence > SUBTLE_NEGATIVE_VALENCE_MIN
    )

    # --- psychosocial context -------------------------------------------
    # None of these predicates is a decision. They are the same kind of
    # precomputed input as `sleep_worsening`: a number compared with a
    # constant, recorded below so the therapist can see both.
    agent2_available = bool(ling["eligible_for_risk"])
    profile = _psychosocial_profile(db, user_id)
    interpersonal_live = profile.interpersonal_risk_is_live
    leave_taking = profile.has_leave_taking_signal
    acute_rupture = profile.has_acute_rupture
    ideation_indirect = bool(ling.get("ideation_indirect")) and agent2_available
    # The "inner half" of a convergence rule: something the person expressed
    # that, alone, is far too ordinary to alert on.
    subtle_inner_signal = bool(ideation_indirect or rumination_high or negative_valence_high)

    n4_facts = _facts_in_categories(db, user_id, N4_FACT_CATEGORIES, CRITICAL_DECLARATION_WINDOW_HOURS)
    n3_facts = _facts_in_categories(db, user_id, N3_FACT_CATEGORIES, CRITICAL_DECLARATION_WINDOW_HOURS)
    persistence_1 = _persistence_detail(db, user_id, "unstable", 1)
    persistence_3 = _persistence_detail(db, user_id, "unstable", STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT)
    persistence_5 = _persistence_detail(db, user_id, "unstable", STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE)

    structural_extreme = structural.score is not None and structural.score < 0.20
    rumination_extreme = isinstance(rumination, (int, float)) and rumination > 0.85
    extreme_convergence = structural_extreme and rumination_extreme and sleep_worsening

    rules = [
        _trace_rule(
            "N4_declaracion_ideacion_o_plan",
            4,
            "Hecho confirmado reciente de ideación activa o planificación",
            [_trace_condition("Hechos N4 en 48 h", len(n4_facts), "gt", 0, bool(n4_facts))],
            bool(n4_facts),
        ),
        _trace_rule(
            "N4_senal_linguistica_ideacion_directa",
            4,
            "Agente 2 detectó ideación directa en una señal vigente",
            [
                _trace_condition("Señal de Agente 2 vigente", agent2_available, "eq", True, agent2_available),
                _trace_condition(
                    "ideation_direct",
                    ling["ideation_direct"] if agent2_available else None,
                    "eq",
                    True,
                    ling["ideation_direct"] if agent2_available else None,
                ),
            ],
            (ling["ideation_direct"] if agent2_available else None),
        ),
        _trace_rule(
            "N4_convergencia_critica_extrema",
            4,
            "Convergencia extrema de score estructural, rumiación y sueño",
            [
                _trace_condition("structural_score", structural.score, "lt", 0.20, structural_extreme if structural.score is not None else None),
                _trace_condition("rumination_score", rumination, "gt", 0.85, rumination_extreme if rumination is not None else None),
                _trace_condition("tendencia de sueño", sleep_detail.label, "eq", "empeorando", sleep_worsening),
            ],
            extreme_convergence if structural.score is not None and rumination is not None else None,
        ),
        _trace_rule(
            "N4_convergencia_interpersonal_despedida",
            4,
            "Ideación indirecta + riesgo interpersonal alto + señales de despedida",
            [
                _trace_condition(
                    "ideación indirecta en una señal vigente",
                    ideation_indirect if agent2_available else None,
                    "eq",
                    True,
                    ideation_indirect if agent2_available else None,
                ),
                _trace_condition(
                    "índice de riesgo interpersonal",
                    profile.interpersonal_risk_index,
                    "gte",
                    psychosocial_service.INTERPERSONAL_RISK_HIGH_MIN,
                    profile.interpersonal_risk_is_high,
                ),
                _trace_condition(
                    "riesgo interpersonal expresado en los últimos 14 días",
                    profile.interpersonal_recent_evidence,
                    "not_empty",
                    True,
                    bool(profile.interpersonal_recent_evidence),
                ),
                _trace_condition(
                    "señal de despedida vigente",
                    profile.leave_taking.summary if profile.leave_taking else None,
                    "eq",
                    True,
                    leave_taking,
                ),
            ],
            (
                None
                if not agent2_available or profile.interpersonal_risk_index is None
                else (ideation_indirect and interpersonal_live and leave_taking)
            ),
        ),
        _trace_rule(
            "N3_declaracion_crisis_consumo",
            3,
            "Hecho confirmado reciente de crisis de consumo",
            [_trace_condition("Hechos N3 en 48 h", len(n3_facts), "gt", 0, bool(n3_facts))],
            bool(n3_facts),
        ),
        _trace_rule(
            "N3_senal_linguistica_crisis_consumo",
            3,
            "Agente 2 detectó crisis de consumo en una señal vigente",
            [
                _trace_condition("Señal de Agente 2 vigente", agent2_available, "eq", True, agent2_available),
                _trace_condition(
                    "consumption_crisis",
                    ling["consumption_crisis"] if agent2_available else None,
                    "eq",
                    True,
                    ling["consumption_crisis"] if agent2_available else None,
                ),
            ],
            (ling["consumption_crisis"] if agent2_available else None),
        ),
        _trace_rule(
            "N3_desconexion_psicosocial_aguda",
            3,
            "Ruptura psicosocial reciente junto a una señal interna sutil",
            [
                _trace_condition(
                    "dominios psicosociales deteriorados en 14 días",
                    profile.acute_deterioration,
                    "not_empty",
                    True,
                    acute_rupture,
                ),
                _trace_condition(
                    "ideación indirecta, rumiación > 0.60 o valencia negativa > 0.70",
                    {
                        "ideation_indirect": ideation_indirect if agent2_available else None,
                        "rumination_score": rumination,
                        "negative_valence": negative_valence,
                    },
                    "any",
                    {
                        "ideation_indirect": True,
                        "rumination_score_gt": SUBTLE_RUMINATION_MIN,
                        "negative_valence_gt": SUBTLE_NEGATIVE_VALENCE_MIN,
                    },
                    subtle_inner_signal,
                ),
            ],
            None if not profile.available else (acute_rupture and subtle_inner_signal),
        ),
        _trace_rule(
            "N3_riesgo_interpersonal_alto",
            3,
            "Sentirse una carga y no pertenecer, expresado en los últimos 14 días",
            [
                _trace_condition(
                    "índice de riesgo interpersonal",
                    profile.interpersonal_risk_index,
                    "gte",
                    psychosocial_service.INTERPERSONAL_RISK_HIGH_MIN,
                    profile.interpersonal_risk_is_high,
                ),
                _trace_condition(
                    "expresado en los últimos 14 días",
                    profile.interpersonal_recent_evidence,
                    "not_empty",
                    True,
                    bool(profile.interpersonal_recent_evidence),
                ),
            ],
            None if profile.interpersonal_risk_index is None else interpersonal_live,
        ),
        _trace_rule(
            "N3_riesgo_recaida_contextual",
            3,
            "Contexto de recaída sostenido con craving al alza",
            [
                _trace_condition(
                    "índice de contexto de recaída",
                    profile.relapse_context_index,
                    "gte",
                    psychosocial_service.RELAPSE_CONTEXT_HIGH_MIN,
                    profile.relapse_context_is_high,
                ),
                _trace_condition(
                    "tendencia de craving",
                    craving_detail.label,
                    "eq",
                    "aumentando",
                    craving_rising,
                ),
            ],
            None
            if profile.relapse_context_index is None
            else bool(profile.relapse_context_is_high and craving_rising),
        ),
        _trace_rule(
            "N3_unstable_persistente_con_convergencia",
            3,
            "Inestabilidad persistente 3 días con otra señal convergente",
            [
                _trace_condition("banda estructural", structural.confidence_band, "eq", "unstable", structural.confidence_band == "unstable"),
                _trace_condition("días inestables distintos", persistence_3["observed_distinct_days"], "gte", 3, persistence_3["passed"]),
                _trace_condition(
                    "sueño empeorando o rumiación > 0.60",
                    {"sleep_trend": sleep_detail.label, "rumination_score": rumination},
                    "any",
                    {"sleep_trend": "empeorando", "rumination_score_gt": 0.60},
                    sleep_worsening or rumination_high,
                ),
            ],
            structural.confidence_band == "unstable" and persistence_3["passed"] and (sleep_worsening or rumination_high),
        ),
        _trace_rule(
            "N3_unstable_persistente",
            3,
            "Inestabilidad estructural persistente durante 5 días",
            [
                _trace_condition("banda estructural", structural.confidence_band, "eq", "unstable", structural.confidence_band == "unstable"),
                _trace_condition("días inestables distintos", persistence_5["observed_distinct_days"], "gte", 5, persistence_5["passed"]),
            ],
            structural.confidence_band == "unstable" and persistence_5["passed"],
        ),
        _trace_rule(
            "N2_desviacion_moderada",
            2,
            "Banda de transición o inicio de inestabilidad",
            [
                _trace_condition(
                    "transition o unstable durante al menos 1 día",
                    {"band": structural.confidence_band, "unstable_days": persistence_1["observed_distinct_days"]},
                    "any",
                    {"band": "transition", "unstable_days_gte": 1},
                    structural.confidence_band == "transition"
                    or (structural.confidence_band == "unstable" and persistence_1["passed"]),
                )
            ],
            structural.confidence_band == "transition"
            or (structural.confidence_band == "unstable" and persistence_1["passed"]),
        ),
        _trace_rule(
            "N2_vulnerabilidad_psicosocial",
            2,
            "Apoyo social bajo o adversidad material alta",
            [
                _trace_condition(
                    "índice de apoyo",
                    profile.support_index,
                    "lte",
                    psychosocial_service.SUPPORT_LOW_MAX,
                    profile.support_is_low,
                ),
                _trace_condition(
                    "índice de adversidad material",
                    profile.material_adversity_index,
                    "gte",
                    psychosocial_service.MATERIAL_ADVERSITY_HIGH_MIN,
                    profile.material_adversity_is_high,
                ),
                _trace_condition(
                    "dominios deteriorados en 14 días",
                    profile.acute_deterioration,
                    "not_empty",
                    True,
                    acute_rupture,
                ),
            ],
            None
            if not profile.available
            else bool(profile.support_is_low or profile.material_adversity_is_high or acute_rupture),
        ),
        _trace_rule(
            "N0_estable",
            0,
            "Situación estable respecto a la línea base",
            [_trace_condition("banda estructural", structural.confidence_band, "eq", "stable", structural.confidence_band == "stable")],
            structural.confidence_band == "stable",
        ),
        _trace_rule(
            "N1_datos_insuficientes_o_sin_criterios",
            1,
            "Datos insuficientes para una desviación estructural",
            [_trace_condition("banda estructural", structural.confidence_band, "eq", "insufficient_data", structural.confidence_band == "insufficient_data")],
            structural.confidence_band == "insufficient_data",
        ),
        _trace_rule(
            "N1_sin_criterios_superiores",
            1,
            "Regla de cierre si no se cumple ningún criterio anterior",
            [_trace_condition("ninguna regla anterior seleccionada", True, "fallback", True, True)],
            True,
        ),
    ]

    # The closing rule is a true fallback: it only matches when none of the
    # preceding ten rules did.  Recording it as an unconditional match would
    # make every historic explanation claim two simultaneous conclusions.
    fallback = rules[-1]
    fallback_matches = not any(rule["matched"] is True for rule in rules[:-1])
    fallback["matched"] = fallback_matches
    fallback["conditions"][0]["actual"] = fallback_matches
    fallback["conditions"][0]["result"] = fallback_matches
    fallback["status"] = "matched_not_selected" if fallback_matches else "not_matched"

    for priority, rule in enumerate(rules, start=1):
        rule["priority"] = priority
    selected = next(rule for rule in rules if rule["matched"] is True)
    selected["selected"] = True
    selected["status"] = "selected"

    reasons = {
        "N4_declaracion_ideacion_o_plan": "Declaración confirmada de ideación activa o planificación (hecho, no inferencia)",
        "N4_senal_linguistica_ideacion_directa": "Señal lingüística reciente de ideación directa (inferencia Agent 2; revisión humana prioritaria)",
        "N4_convergencia_critica_extrema": "Convergencia extrema: score estructural muy bajo + rumiación alta + sueño empeorando",
        "N4_convergencia_interpersonal_despedida": (
            "Convergencia interpersonal: ideación indirecta + sentirse una carga / no pertenecer "
            "+ señales de despedida recientes (inferencias sobre textos concretos; revisión humana inmediata)"
        ),
        "N3_declaracion_crisis_consumo": "Declaración de crisis de consumo (alarma profesional, no emergencia 112 automática)",
        "N3_senal_linguistica_crisis_consumo": "Señal lingüística de crisis de consumo (inferencia; revisión profesional)",
        "N3_desconexion_psicosocial_aguda": (
            "Ruptura psicosocial reciente (apoyo, vivienda, economía o pérdida) junto a una señal "
            "interna sutil que por sí sola no habría disparado nada"
        ),
        "N3_riesgo_interpersonal_alto": (
            "Carga percibida y pertenencia frustrada altas, expresadas en los últimos 14 días "
            "(constructos interpersonales; revisión profesional)"
        ),
        "N3_riesgo_recaida_contextual": (
            "Contexto social de consumo sostenido con tendencia de craving al alza (riesgo de recaída)"
        ),
        "N3_unstable_persistente_con_convergencia": "Desviación estructural persistente (≥3 días inestable) con convergencia de señales",
        "N3_unstable_persistente": "structural_score en banda inestable de forma sostenida (≥5 días)",
        "N2_desviacion_moderada": "Desviación moderada o inicio de inestabilidad (prevención, sin alerta profesional automática)",
        "N2_vulnerabilidad_psicosocial": (
            "Vulnerabilidad psicosocial: apoyo bajo, adversidad material alta o un deterioro reciente "
            "del contexto (prevención, sin alerta profesional automática)"
        ),
        "N0_estable": "Situación estable respecto a la línea base personal",
        "N1_datos_insuficientes_o_sin_criterios": "Sin criterios de nivel superior (datos insuficientes o sin desviación clara)",
        "N1_sin_criterios_superiores": "Situación dentro de parámetros de autogestión",
    }
    reason = reasons[selected["code"]]

    input_signals = {
        "structural_score": structural.score,
        "confidence_band": structural.confidence_band,
        "z_scores": structural.z_scores,
        "linguistic": linguistic,
        "linguistic_signal_id": ling["signal_id"],
        "linguistic_signal_timestamp": ling["signal_timestamp"],
        "linguistic_signal_eligible_for_risk": agent2_available,
        "linguistic_flags": {
            "ideation_direct": ling["ideation_direct"] if agent2_available else None,
            "ideation_indirect": ideation_indirect if agent2_available else None,
            "consumption_crisis": ling["consumption_crisis"] if agent2_available else None,
        },
        "sleep_trend": sleep_detail.label,
        "sleep_trend_slope": sleep_detail.slope,
        "craving_trend": craving_detail.label,
        "craving_trend_slope": craving_detail.slope,
        "rumination_threshold_exceeded": rumination_high if rumination is not None else None,
        # The psychosocial summary travels with the assessment so the alert,
        # the panel and any later audit all read the same numbers, taken at
        # decision time rather than recomputed from newer observations.
        "psychosocial": {
            "available": profile.available,
            "support_index": profile.support_index,
            "material_adversity_index": profile.material_adversity_index,
            "interpersonal_risk_index": profile.interpersonal_risk_index,
            "relapse_context_index": profile.relapse_context_index,
            "acute_deterioration": profile.acute_deterioration,
            "interpersonal_recent_evidence": profile.interpersonal_recent_evidence,
            "leave_taking_signal": bool(profile.leave_taking),
            "risk_domains": profile.risk_domains,
            "protective_domains": profile.protective_domains,
            "known_domain_count": profile.known_domain_count,
        },
    }
    input_facts = {
        "n4_declarations": n4_facts,
        "n3_declarations": n3_facts,
        "critical_declarations": n4_facts + n3_facts,
    }

    variable_calculations = []
    for key in baseline_service.VARIABLES:
        baseline_stats = structural.baseline_stats.get(key, {})
        baseline_mean = baseline_stats.get("mean")
        recent_mean = structural.recent_means.get(key)
        z_score = structural.z_scores.get(key)
        variable_calculations.append(
            {
                "key": key,
                "transformation": "10 - craving" if key == "craving_inv" else "identity",
                "baseline_mean": baseline_mean,
                "baseline_population_std": baseline_stats.get("std"),
                "recent_mean": recent_mean,
                "difference": round(recent_mean - baseline_mean, 3)
                if isinstance(recent_mean, (int, float)) and isinstance(baseline_mean, (int, float))
                else None,
                "formula": "(recent_mean - baseline_mean) / baseline_population_std",
                "zero_std_policy": "z_equals_zero",
                "z_score": z_score,
                "absolute_z": abs(z_score) if isinstance(z_score, (int, float)) else None,
            }
        )

    matched_codes = [rule["code"] for rule in rules if rule["matched"] is True]
    calculation_trace = {
        "schema_version": "risk-explanation-v1",
        "engine": {
            "name": "deterministic-risk-engine",
            "version": MODEL_VERSION,
            "evaluated_at": _utc_iso(evaluated_at),
            "evaluation_order": [rule["code"] for rule in rules],
            "thresholds": {
                "baseline_window_days": baseline_service.BASELINE_WINDOW_DAYS,
                "recent_window_days": baseline_service.RECENT_WINDOW_DAYS,
                "minimum_baseline_checkins": baseline_service.MIN_CHECKINS_FOR_BASELINE,
                "agent2_freshness_hours": 12,
                "critical_fact_window_hours": CRITICAL_DECLARATION_WINDOW_HOURS,
                "structural_stable_gte": 0.60,
                "structural_transition_gte": 0.35,
                "structural_extreme_lt": 0.20,
                "rumination_high_gt": SUBTLE_RUMINATION_MIN,
                "rumination_extreme_gt": 0.85,
                "sleep_worsening_slope_lt": -0.15,
                "craving_rising_slope_gt": 0.15,
                "negative_valence_high_gt": SUBTLE_NEGATIVE_VALENCE_MIN,
                "psychosocial_support_low_lte": psychosocial_service.SUPPORT_LOW_MAX,
                "psychosocial_material_adversity_high_gte": psychosocial_service.MATERIAL_ADVERSITY_HIGH_MIN,
                "psychosocial_interpersonal_high_gte": psychosocial_service.INTERPERSONAL_RISK_HIGH_MIN,
                "psychosocial_relapse_context_high_gte": psychosocial_service.RELAPSE_CONTEXT_HIGH_MIN,
                "psychosocial_recent_change_days": psychosocial_service.RECENT_CHANGE_DAYS,
            },
        },
        "inputs": {
            "structural": {
                "baseline_sample_count": structural.baseline_n,
                "recent_sample_count": structural.recent_n,
                "variables": variable_calculations,
                "composite": {
                    "formula": "mean(abs(z_score))",
                    "composite_z": structural.composite_z,
                    "score_formula": "clamp(1 - composite_z / 3, 0, 1)",
                    "score": structural.score,
                    "band": structural.confidence_band,
                },
            },
            "sleep_trend": {
                "points": [
                    {
                        "checkin_id": str(row.id),
                        "created_at": _utc_iso(row.created_at),
                        "x": index,
                        "sleep_hours": float(row.sleep_hours),
                    }
                    for index, row in enumerate(ordered_checkins)
                ],
                "sample_count": sleep_detail.sample_count,
                "slope": sleep_detail.slope,
                "formula": "sum((x-x_mean)*(y-y_mean)) / sum((x-x_mean)^2)",
                "classification": sleep_detail.label,
            },
            "agent2": {
                "freshness_window_hours": ling["freshness_window_hours"],
                "selected_signal_id": ling["signal_id"],
                "signal_timestamp": ling["signal_timestamp"],
                "eligible_for_risk": agent2_available,
                "values_used": linguistic if agent2_available else None,
            },
            "confirmed_facts": {"window_hours": CRITICAL_DECLARATION_WINDOW_HOURS, "n4": n4_facts, "n3": n3_facts},
            "persistence": {"unstable_1d": persistence_1, "unstable_3d": persistence_3, "unstable_5d": persistence_5},
            "craving_trend": {
                "points": [
                    {
                        "checkin_id": str(row.id),
                        "created_at": _utc_iso(row.created_at),
                        "x": index,
                        "craving": float(row.craving),
                    }
                    for index, row in enumerate(ordered_checkins)
                ],
                "sample_count": craving_detail.sample_count,
                "slope": craving_detail.slope,
                "formula": "sum((x-x_mean)*(y-y_mean)) / sum((x-x_mean)^2)",
                "classification": craving_detail.label,
            },
            # Full psychosocial snapshot, including the quote behind every
            # domain, so a level raised by social context can be audited from
            # the stored decision alone.
            "psychosocial": profile.as_dict(),
        },
        "derivations": {
            "structural_score": structural.score,
            "confidence_band": structural.confidence_band,
            "sleep_worsening": sleep_worsening,
            "craving_rising": craving_rising,
            "rumination_score": rumination,
            "rumination_high": rumination_high if rumination is not None else None,
            "negative_valence": negative_valence,
            "negative_valence_high": negative_valence_high if negative_valence is not None else None,
            "agent2_signal_available": agent2_available,
            "ideation_indirect": ideation_indirect if agent2_available else None,
            "n4_fact_count": len(n4_facts),
            "n3_fact_count": len(n3_facts),
            "psychosocial_available": profile.available,
            "psychosocial_acute_rupture": acute_rupture,
            "psychosocial_interpersonal_live": interpersonal_live,
            "psychosocial_leave_taking": leave_taking,
            "psychosocial_subtle_inner_signal": subtle_inner_signal,
            "psychosocial_acute_domains_labels": [domain_label(key) for key in profile.acute_deterioration],
        },
        "rules": rules,
        "conclusion": {
            "level": selected["target_level"],
            "selected_rule_code": selected["code"],
            "matched_rule_codes": matched_codes,
            "reason": reason,
        },
    }

    return RiskDecision(
        level=selected["target_level"],
        triggering_rules=[selected["code"]],
        reason=reason,
        input_signals=input_signals,
        input_facts=input_facts,
        calculation_trace=calculation_trace,
        linguistic_signal_id=ling["_signal_uuid"],
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


def run_and_persist(
    db: Session,
    user_id,
    *,
    correlation_id=None,
    agent2_trace_id=None,
    linguistic_signal_id=None,
) -> RiskAssessment:
    """
    Full pipeline: evaluate -> persist assessment -> create professional_alerts
    if level >= 3 (deduped) -> enqueue notifications.
    """
    decision = calculate_risk_level(db, user_id, linguistic_signal_id=linguistic_signal_id)

    calculation_trace = dict(decision.calculation_trace or {})
    calculation_trace["correlation_id"] = str(correlation_id) if correlation_id else None
    calculation_trace["agent2_attempt_trace_id"] = str(agent2_trace_id) if agent2_trace_id else None
    calculation_trace["linguistic_signal_id_used"] = (
        str(decision.linguistic_signal_id) if decision.linguistic_signal_id else None
    )

    assessment = RiskAssessment(
        user_id=user_id,
        alert_level=decision.level,
        triggering_rules=decision.triggering_rules,
        input_signals=decision.input_signals,
        input_facts=decision.input_facts,
        confidence=decision.input_signals.get("structural_score"),
        assessment_reason=decision.reason,
        model_version=MODEL_VERSION,
        correlation_id=correlation_id,
        agent2_trace_id=agent2_trace_id,
        linguistic_signal_id_used=decision.linguistic_signal_id,
        calculation_trace=calculation_trace,
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

    final_trace = dict(assessment.calculation_trace or {})
    conclusion = dict(final_trace.get("conclusion") or {})
    conclusion["generated_alert_id"] = str(assessment.generated_alert_id) if assessment.generated_alert_id else None
    final_trace["conclusion"] = conclusion
    assessment.calculation_trace = final_trace
    db.commit()

    return assessment


def _as_domain_list(input_signals: dict, key: str) -> list[str]:
    psychosocial = input_signals.get("psychosocial") if isinstance(input_signals, dict) else None
    values = psychosocial.get(key) if isinstance(psychosocial, dict) else None
    return [str(value) for value in values] if isinstance(values, list) else []


def _alert_title(decision: RiskDecision) -> str:
    if decision.level == 4:
        if "N4_convergencia_interpersonal_despedida" in decision.triggering_rules:
            return "ALERTA NIVEL 4 – EMERGENCIA (convergencia interpersonal y señales de despedida)"
        return "ALERTA NIVEL 4 – EMERGENCIA"
    if decision.level == 3:
        if "N3_declaracion_crisis_consumo" in decision.triggering_rules:
            return "Alerta Nivel 3 – Crisis de consumo declarada"
        if "N3_senal_linguistica_crisis_consumo" in decision.triggering_rules:
            return "Alerta Nivel 3 – Señal lingüística de crisis de consumo"
        if "N3_desconexion_psicosocial_aguda" in decision.triggering_rules:
            domains = ", ".join(
                domain_label(key)
                for key in _as_domain_list(decision.input_signals, "acute_deterioration")
            )
            return f"Alerta Nivel 3 – Ruptura psicosocial reciente{f' ({domains})' if domains else ''}"
        if "N3_riesgo_interpersonal_alto" in decision.triggering_rules:
            return "Alerta Nivel 3 – Carga percibida y falta de pertenencia"
        if "N3_riesgo_recaida_contextual" in decision.triggering_rules:
            return "Alerta Nivel 3 – Contexto de recaída con craving al alza"
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
