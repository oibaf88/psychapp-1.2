"""
Human-readable clinical views over data the deterministic engine already
persisted.

The professional panel used to render the stored ``calculation_trace``
JSON almost verbatim. That is faithful but unreadable: a therapist opening
a patient saw identifiers, SHA-256 digests and nested objects instead of
an answer to "what happened, when, because of which sentence, and what do
I do now".

This module turns the *same* stored evidence into Spanish prose,
chart-ready series and explicit links back to the source text. It never
recomputes a decision and never calls an LLM: the level, the rules and the
numbers are read from what the risk engine wrote at decision time, so a
historic explanation cannot drift when newer data arrives.

Two things this module exists to make explicit, because both confused
readers of the previous UI:

1. ``structural_score`` is NOT a risk score. It measures how similar the
   last 7 days of check-ins are to the patient's own 21-day baseline.
   1.00 means "indistinguishable from their own normal", 0.00 means "very
   far from their own normal". A high score therefore says *stable*, not
   *safe*.
2. Because of (1), a patient can legitimately sit at ``0.91 / stable`` and
   still be at alert level 4: the level was raised by a confirmed fact or
   by a linguistic signal in chat/diary, neither of which is part of the
   structural score at all. Every explanation below states which family of
   evidence actually drove the level, and says so in the same sentence as
   the score when the two appear to disagree.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    Agent2AnalysisTrace,
    AlfaSignal,
    ChatMessage,
    CheckIn,
    ConfirmedFact,
    DiaryEntry,
    ProfessionalAlert,
    PsychosocialObservation,
    RiskAssessment,
)
from app.services import baseline as baseline_service
from app.services import psychosocial as psychosocial_service

EXCERPT_CHARS = 320

# --------------------------------------------------------------- catalog ---
# One entry per deterministic rule. `family` is what the therapist actually
# needs first: which KIND of evidence raised the level.
FAMILY_CONFIRMED_FACT = "hecho_confirmado"
FAMILY_LINGUISTIC = "senal_linguistica"
FAMILY_STRUCTURAL = "desviacion_estructural"
FAMILY_CONVERGENCE = "convergencia"
FAMILY_PSYCHOSOCIAL = "contexto_psicosocial"
FAMILY_NONE = "sin_criterios"

FAMILY_LABELS = {
    FAMILY_CONFIRMED_FACT: "Hecho confirmado",
    FAMILY_LINGUISTIC: "Señal lingüística (Agente 2)",
    FAMILY_STRUCTURAL: "Desviación estructural (check-ins)",
    FAMILY_CONVERGENCE: "Convergencia de varias señales",
    FAMILY_PSYCHOSOCIAL: "Contexto psicosocial (Agente 4)",
    FAMILY_NONE: "Ningún criterio de nivel superior",
}

FAMILY_EVIDENCE_KIND = {
    FAMILY_CONFIRMED_FACT: "Declaración registrada por el paciente o por un profesional. Es un HECHO, no una inferencia.",
    FAMILY_LINGUISTIC: "Inferencia de un modelo de lenguaje sobre un texto concreto del paciente. Requiere lectura del texto original.",
    FAMILY_STRUCTURAL: "Estadística sobre los check-ins diarios comparados con la línea base del propio paciente.",
    FAMILY_CONVERGENCE: "Varias señales independientes apuntando en la misma dirección a la vez.",
    FAMILY_PSYCHOSOCIAL: (
        "Determinantes sociales extraídos por un modelo de lenguaje de lo que el paciente contó, "
        "ponderados después por una fórmula fija. Cada observación conserva la frase literal que la sostiene."
    ),
    FAMILY_NONE: "No se ha cumplido ningún criterio de nivel 2 o superior.",
}

RULE_CATALOG: dict[str, dict[str, Any]] = {
    "N4_declaracion_ideacion_o_plan": {
        "family": FAMILY_CONFIRMED_FACT,
        "level": 4,
        "title": "Ideación activa o planificación declarada",
        "plain": (
            "En las últimas 48 horas se registró un hecho confirmado de la categoría "
            "«ideación activa» o «planificación». El motor no ha inferido nada: alguien "
            "(el paciente o un profesional) lo declaró explícitamente."
        ),
        "what_now": (
            "Contacto directo con el paciente hoy. Revisar el plan de seguridad y valorar "
            "derivación urgente. El paciente ya ha visto en la app el mensaje fijo con 024 y 112."
        ),
    },
    "N4_senal_linguistica_ideacion_directa": {
        "family": FAMILY_LINGUISTIC,
        "level": 4,
        "title": "Ideación directa detectada en un texto del paciente",
        "plain": (
            "El Agente 2 marcó `ideation_direct = true` al analizar un mensaje de chat o una "
            "entrada de diario escrita en las últimas 12 horas. Es una INFERENCIA de un modelo "
            "de lenguaje sobre un texto concreto, no una declaración del paciente."
        ),
        "what_now": (
            "Lee el texto original que aparece junto a esta evaluación antes de decidir. Si la "
            "lectura del modelo es correcta, actúa como en una alerta de nivel 4. Si es un falso "
            "positivo (ironía, cita, letra de canción), regístralo como hecho de categoría "
            "«corrección» y descarta la alerta indicando el motivo."
        ),
    },
    "N4_convergencia_critica_extrema": {
        "family": FAMILY_CONVERGENCE,
        "level": 4,
        "title": "Convergencia extrema de tres señales independientes",
        "plain": (
            "Se dieron a la vez: score estructural por debajo de 0.20 (check-ins muy alejados de "
            "su línea base), rumiación por encima de 0.85 en el último texto analizado, y "
            "tendencia de sueño empeorando en los últimos 7 check-ins."
        ),
        "what_now": (
            "Ninguna de las tres señales bastaría por sí sola. Revisa las tres gráficas de esta "
            "ficha y contacta con el paciente."
        ),
    },
    "N3_declaracion_crisis_consumo": {
        "family": FAMILY_CONFIRMED_FACT,
        "level": 3,
        "title": "Crisis de consumo declarada",
        "plain": (
            "Hecho confirmado de categoría «crisis de consumo» en las últimas 48 horas. "
            "Es una declaración, no una inferencia."
        ),
        "what_now": (
            "Revisión profesional en cuanto sea posible. No dispara protocolo de emergencia "
            "automático: el sistema distingue crisis de consumo de riesgo autolítico."
        ),
    },
    "N3_senal_linguistica_crisis_consumo": {
        "family": FAMILY_LINGUISTIC,
        "level": 3,
        "title": "Crisis de consumo inferida de un texto",
        "plain": (
            "El Agente 2 marcó `consumption_crisis = true` sobre un texto de las últimas 12 horas. "
            "Es una inferencia sobre un texto concreto."
        ),
        "what_now": "Lee el texto original antes de contactar. Contrasta con los check-ins de craving.",
    },
    "N3_unstable_persistente_con_convergencia": {
        "family": FAMILY_CONVERGENCE,
        "level": 3,
        "title": "Inestabilidad de 3 días con otra señal convergente",
        "plain": (
            "El score estructural lleva al menos 3 días distintos en banda «unstable» Y además el "
            "sueño empeora o la rumiación supera 0.60. La persistencia se cuenta en días "
            "naturales distintos, no en número de registros, para que varios check-ins del mismo "
            "día no disparen la regla."
        ),
        "what_now": "Deterioro sostenido, no un mal día. Buen momento para adelantar la sesión.",
    },
    "N3_unstable_persistente": {
        "family": FAMILY_STRUCTURAL,
        "level": 3,
        "title": "Inestabilidad estructural sostenida 5 días",
        "plain": (
            "El score estructural lleva al menos 5 días naturales distintos en banda «unstable», "
            "sin necesidad de ninguna otra señal."
        ),
        "what_now": (
            "Revisa qué variable concreta se ha desviado (ánimo, craving, sueño o autoeficacia) "
            "en la gráfica de z-scores de esta ficha."
        ),
    },
    "N3_desestabilizacion_psicosocial_aguda": {
        "family": FAMILY_PSYCHOSOCIAL,
        "level": 3,
        "title": "Cambio psicosocial reciente coincidiendo con deterioro",
        "plain": (
            "En los últimos 14 días el paciente contó un cambio adverso en su contexto —vivienda, "
            "convivencia, apoyo, dinero, una pérdida, o desvinculación del tratamiento— Y además hay otra "
            "señal de deterioro (sueño empeorando, rumiación > 0.60, o banda estructural no estable). "
            "Ninguna de las dos cosas por separado habría llegado a nivel 3."
        ),
        "what_now": (
            "Este es el patrón que suele preceder a una crisis o a una recaída antes de que el estado de "
            "ánimo cambie. Mira la pestaña «Contexto psicosocial»: verás la frase literal del cambio y su "
            "fecha. Contacta y explora ese cambio concreto, no el estado general."
        ),
    },
    "N3_convergencia_psicosocial_estructural": {
        "family": FAMILY_PSYCHOSOCIAL,
        "level": 3,
        "title": "Vulnerabilidad psicosocial alta con inestabilidad estructural",
        "plain": (
            "El índice de vulnerabilidad psicosocial está en 0.60 o más Y la banda estructural es "
            "«unstable». Son dos medidas independientes —una de su situación de vida, otra de sus "
            "check-ins— señalando a la vez."
        ),
        "what_now": (
            "Revisa qué dominios pesan más en el índice (vivienda, apoyo, economía…) y contrasta con los "
            "z-scores. Suele indicar que el deterioro tiene una causa situacional identificable."
        ),
    },
    "N2_vulnerabilidad_psicosocial": {
        "family": FAMILY_PSYCHOSOCIAL,
        "level": 2,
        "title": "Vulnerabilidad psicosocial moderada",
        "plain": (
            "El índice psicosocial alcanza 0.50 sin que ninguna otra señal converja. Nivel de prevención: "
            "el índice por sí solo NUNCA genera alerta profesional, por diseño."
        ),
        "what_now": (
            "Información de seguimiento. Buen momento para trabajar el contexto (recursos, vivienda, red de "
            "apoyo) antes de que converja con otra señal."
        ),
    },
    "N2_desviacion_moderada": {
        "family": FAMILY_STRUCTURAL,
        "level": 2,
        "title": "Desviación moderada o inicio de inestabilidad",
        "plain": (
            "Banda «transition», o primer día en banda «unstable». Nivel de prevención: la app "
            "refuerza herramientas de autorregulación con el paciente."
        ),
        "what_now": "No genera alerta profesional automática. Es información de seguimiento.",
    },
    "N0_estable": {
        "family": FAMILY_NONE,
        "level": 0,
        "title": "Estable respecto a su propia línea base",
        "plain": (
            "Los check-ins de los últimos 7 días se parecen a los de su ventana base de 21 días. "
            "Esto describe estabilidad, no ausencia de sufrimiento."
        ),
        "what_now": "Seguimiento habitual.",
    },
    "N1_datos_insuficientes_o_sin_criterios": {
        "family": FAMILY_NONE,
        "level": 1,
        "title": "Datos insuficientes para calcular desviación",
        "plain": (
            "No hay suficientes check-ins (mínimo 5 en 21 días) para construir una línea base "
            "personal, así que el score estructural no existe todavía."
        ),
        "what_now": (
            "El nivel 1 aquí significa «no lo sé», no «bajo riesgo». Anima al paciente a hacer "
            "check-ins diarios."
        ),
    },
    "N1_sin_criterios_superiores": {
        "family": FAMILY_NONE,
        "level": 1,
        "title": "Sin criterios de nivel superior",
        "plain": "Regla de cierre: no se cumplió ninguna de las anteriores.",
        "what_now": "Seguimiento habitual.",
    },
}

LEVEL_LABELS = {
    0: "Nivel 0 · Autogestión",
    1: "Nivel 1 · Autogestión / datos insuficientes",
    2: "Nivel 2 · Prevención",
    3: "Nivel 3 · Alarma profesional",
    4: "Nivel 4 · Emergencia",
}

LEVEL_MEANING = {
    0: "El paciente se gestiona con las herramientas de la app. No requiere acción del terapeuta.",
    1: "Sin criterios de nivel superior, o sin datos suficientes para evaluarlos. No es una garantía de seguridad.",
    2: "Prevención. La app refuerza autorregulación con el paciente. NO genera alerta profesional automática.",
    3: "Alarma profesional: requiere revisión humana en cuanto sea posible. Genera alerta y notificación.",
    4: "Emergencia: requiere atención inmediata. El paciente ve en su pantalla el bloque fijo con 024 y 112.",
}

VARIABLE_LABELS = {
    "mood": "Ánimo",
    "craving_inv": "Craving (invertido)",
    "sleep_hours": "Horas de sueño",
    "self_efficacy": "Autoeficacia",
}

VARIABLE_NOTES = {
    "mood": "0–10 declarado en el check-in. Más alto es mejor.",
    "craving_inv": "Se calcula como 10 − craving, para que, igual que el resto, más alto sea mejor.",
    "sleep_hours": "Horas declaradas. Se compara con su propia media, no con las 8 h de manual.",
    "self_efficacy": "0–10, confianza percibida en poder manejar la situación. Más alto es mejor.",
}

BAND_LABELS = {
    "stable": "estable",
    "transition": "transición",
    "unstable": "inestable",
    "insufficient_data": "datos insuficientes",
}

BAND_MEANING = {
    "stable": "score ≥ 0.60 — los últimos 7 días se parecen a su línea base.",
    "transition": "0.35 ≤ score < 0.60 — desviación moderada respecto a su línea base.",
    "unstable": "score < 0.35 — los últimos 7 días se alejan mucho de su línea base.",
    "insufficient_data": "no hay línea base personal todavía (mínimo 5 check-ins en 21 días).",
}


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _excerpt(text: str | None, limit: int = EXCERPT_CHARS) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ------------------------------------------------------- level narrative ---
def rule_info(code: str | None) -> dict[str, Any]:
    if code and code in RULE_CATALOG:
        return RULE_CATALOG[code]
    return {
        "family": FAMILY_NONE,
        "level": None,
        "title": code or "Regla desconocida",
        "plain": "Esta evaluación se guardó con una versión del motor que esta pantalla no conoce.",
        "what_now": "Revisa los datos crudos en «detalle técnico».",
    }


def trace_inputs(assessment: RiskAssessment) -> dict:
    """The `inputs` block of the persisted calculation trace, if any."""
    return _as_dict(_as_dict(assessment.calculation_trace).get("inputs"))


def selected_rule_code(assessment: RiskAssessment) -> str | None:
    rules = assessment.triggering_rules
    if isinstance(rules, list) and rules:
        return str(rules[0])
    if isinstance(rules, str) and rules:
        return rules
    conclusion = _as_dict(_as_dict(assessment.calculation_trace).get("conclusion"))
    code = conclusion.get("selected_rule_code")
    return str(code) if code else None


def level_explanation(
    assessment: RiskAssessment | None,
    *,
    driver_evidence: dict | None = None,
) -> dict[str, Any]:
    """Plain-Spanish answer to 'why is this patient at this level right now'."""
    if assessment is None:
        return {
            "level": None,
            "level_label": "Sin evaluación",
            "level_meaning": "Este paciente todavía no tiene ninguna evaluación de riesgo guardada.",
            "headline": "Sin evaluación de riesgo todavía.",
            "rule_code": None,
            "rule_title": None,
            "rule_explanation": None,
            "driver_family": FAMILY_NONE,
            "driver_family_label": FAMILY_LABELS[FAMILY_NONE],
            "driver_evidence_kind": None,
            "what_now": "El paciente debe registrar al menos un check-in, una entrada de diario o un mensaje de chat.",
            "structural_reconciliation": None,
            "driver_evidence": None,
            "calculated_at": None,
            "assessment_id": None,
            "generated_alert_id": None,
        }

    code = selected_rule_code(assessment)
    info = rule_info(code)
    family = info["family"]
    level = assessment.alert_level
    signals = _as_dict(assessment.input_signals)
    score = _number(signals.get("structural_score"))
    band = signals.get("confidence_band")

    if family == FAMILY_CONFIRMED_FACT:
        headline = f"Nivel {level} por un HECHO declarado, no por una inferencia del sistema."
    elif family == FAMILY_LINGUISTIC:
        headline = f"Nivel {level} por lo que el paciente ESCRIBIÓ, analizado por el Agente 2."
    elif family == FAMILY_STRUCTURAL:
        headline = f"Nivel {level} por la evolución de sus CHECK-INS frente a su línea base."
    elif family == FAMILY_CONVERGENCE:
        headline = f"Nivel {level} porque varias señales independientes coincidieron."
    elif family == FAMILY_PSYCHOSOCIAL:
        headline = f"Nivel {level} por el CONTEXTO SOCIAL que el paciente ha contado, no por su estado de ánimo."
    else:
        headline = f"Nivel {level}: no se cumplió ningún criterio de nivel superior."

    # The reconciliation sentence exists because "0.91 estable" next to
    # "alerta nivel 4" reads as a contradiction unless it is spelled out.
    reconciliation = None
    if score is not None and family in (FAMILY_CONFIRMED_FACT, FAMILY_LINGUISTIC) and level >= 3:
        reconciliation = (
            f"El score estructural es {score:.2f} ({BAND_LABELS.get(band, band)}), es decir, sus check-ins "
            f"diarios siguen pareciéndose a su línea base. No hay contradicción: el score estructural solo "
            f"mide check-ins, y este nivel {level} no lo ha disparado el score sino "
            f"{'un hecho declarado' if family == FAMILY_CONFIRMED_FACT else 'un texto concreto del paciente'}. "
            f"Una persona puede seguir durmiendo y puntuando como siempre y aun así escribir, o declarar, "
            f"algo que exige atención hoy."
        )
    elif score is not None and family == FAMILY_PSYCHOSOCIAL:
        psycho = _as_dict(signals.get("psychosocial"))
        index = _number(psycho.get("index"))
        reconciliation = (
            f"El score estructural es {score:.2f} ({BAND_LABELS.get(band, band)}) y el índice psicosocial "
            f"{index if index is not None else '—'}. Son dos cosas distintas: el score mide sus check-ins "
            f"diarios; el índice mide su situación de vida (vivienda, apoyo, dinero, pérdidas, vínculo con el "
            f"tratamiento). Este nivel lo ha disparado el contexto, que suele moverse antes que el ánimo."
        )
    elif score is not None and family in (FAMILY_STRUCTURAL, FAMILY_CONVERGENCE):
        reconciliation = (
            f"Aquí el score estructural SÍ es el motivo: {score:.2f} "
            f"({BAND_LABELS.get(band, band)}). Revisa qué variable se ha desviado en la gráfica de z-scores."
        )
    elif score is None:
        reconciliation = (
            "No hay score estructural en esta evaluación: el paciente aún no tiene línea base "
            "(mínimo 5 check-ins en 21 días) o no ha hecho check-ins en los últimos 7 días."
        )

    return {
        "level": level,
        "level_label": LEVEL_LABELS.get(level, f"Nivel {level}"),
        "level_meaning": LEVEL_MEANING.get(level, ""),
        "headline": headline,
        "rule_code": code,
        "rule_title": info["title"],
        "rule_explanation": info["plain"],
        "driver_family": family,
        "driver_family_label": FAMILY_LABELS.get(family, family),
        "driver_evidence_kind": FAMILY_EVIDENCE_KIND.get(family),
        "what_now": info["what_now"],
        "structural_reconciliation": reconciliation,
        "driver_evidence": driver_evidence,
        "calculated_at": _utc_iso(assessment.calculated_at),
        "assessment_id": str(assessment.id),
        "generated_alert_id": str(assessment.generated_alert_id) if assessment.generated_alert_id else None,
    }


# -------------------------------------------------- structural narrative ---
def structural_explanation(assessment: RiskAssessment | None) -> dict[str, Any]:
    """Explain the structural score in words, with a per-variable breakdown.

    Adds the directional reading the raw score cannot give: the composite is
    a mean of ABSOLUTE z-scores, so improving a lot and deteriorating a lot
    both push the score down. Splitting the variables into adverse and
    favourable movements is what makes a low score actionable.
    """
    empty = {
        "score": None,
        "band": None,
        "band_label": None,
        "band_meaning": None,
        "scale_note": (
            "El score estructural va de 0.00 a 1.00 y mide SIMILITUD con la línea base del propio "
            "paciente, no gravedad clínica. 1.00 = sus últimos 7 días son indistinguibles de sus "
            "21 días previos. 0.00 = se han alejado mucho. Un score alto significa «sin cambios», "
            "nunca «sin riesgo»."
        ),
        "summary": "Sin score estructural en esta evaluación.",
        "direction_summary": None,
        "variables": [],
        "composite_z": None,
        "adverse_composite_z": None,
        "favourable_composite_z": None,
        "baseline_sample_count": None,
        "recent_sample_count": None,
        "sleep_trend": None,
        "sleep_trend_slope": None,
        "caveats": [],
    }
    if assessment is None:
        return empty

    signals = _as_dict(assessment.input_signals)
    trace = _as_dict(assessment.calculation_trace)
    structural = _as_dict(_as_dict(trace.get("inputs")).get("structural"))
    composite = _as_dict(structural.get("composite"))

    score = _number(signals.get("structural_score"))
    band = signals.get("confidence_band")
    if score is None and band is None:
        return empty

    z_scores = _as_dict(signals.get("z_scores"))
    trace_variables = {row.get("key"): row for row in _as_list(structural.get("variables")) if isinstance(row, dict)}

    variables: list[dict[str, Any]] = []
    adverse: list[float] = []
    favourable: list[float] = []
    for key in baseline_service.VARIABLES:
        row = _as_dict(trace_variables.get(key))
        z = _number(row.get("z_score"))
        if z is None:
            z = _number(z_scores.get(key))
        baseline_mean = _number(row.get("baseline_mean"))
        recent_mean = _number(row.get("recent_mean"))
        if z is None:
            direction = "sin_datos"
            reading = "Sin z-score guardado para esta variable."
        elif abs(z) < 0.5:
            direction = "igual"
            reading = "Prácticamente igual que su línea base."
        elif z < 0:
            direction = "peor"
            reading = "Por DEBAJO de su línea base (movimiento adverso)."
        else:
            direction = "mejor"
            reading = "Por ENCIMA de su línea base (movimiento favorable)."
        if z is not None:
            (adverse if z < 0 else favourable).append(abs(z))
        variables.append(
            {
                "key": key,
                "label": VARIABLE_LABELS.get(key, key),
                "note": VARIABLE_NOTES.get(key),
                "baseline_mean": baseline_mean,
                "baseline_std": _number(row.get("baseline_population_std")),
                "recent_mean": recent_mean,
                "difference": _number(row.get("difference")),
                "z_score": z,
                "abs_z": abs(z) if z is not None else None,
                "direction": direction,
                "reading": reading,
            }
        )

    adverse_z = round(sum(adverse) / len(adverse), 3) if adverse else 0.0
    favourable_z = round(sum(favourable) / len(favourable), 3) if favourable else 0.0
    composite_z = _number(composite.get("composite_z"))

    worst = max(
        (v for v in variables if v["abs_z"] is not None and v["direction"] == "peor"),
        key=lambda v: v["abs_z"],
        default=None,
    )
    best = max(
        (v for v in variables if v["abs_z"] is not None and v["direction"] == "mejor"),
        key=lambda v: v["abs_z"],
        default=None,
    )

    if score is None:
        summary = "El paciente no tiene línea base personal todavía, así que no hay score estructural."
    elif band == "stable":
        summary = (
            f"{score:.2f} · estable. Sus check-ins de los últimos 7 días se parecen a los de sus 21 días "
            f"previos. Esto describe continuidad, no bienestar."
        )
    elif band == "transition":
        summary = (
            f"{score:.2f} · transición. Hay una desviación moderada respecto a su propia normalidad."
        )
    else:
        summary = (
            f"{score:.2f} · inestable. Sus últimos 7 días se alejan claramente de su línea base personal."
        )

    if adverse_z == 0 and favourable_z == 0:
        direction_summary = "Sin desviación apreciable en ninguna variable."
    elif favourable_z > adverse_z and best is not None:
        direction_summary = (
            f"La desviación es mayoritariamente FAVORABLE: lo que más se ha movido es «{best['label']}», "
            f"y hacia arriba. Ojo: un score bajo no implica empeoramiento — el cálculo usa valores "
            f"absolutos, así que una mejora grande también baja el score."
        )
    elif worst is not None:
        direction_summary = (
            f"La desviación es mayoritariamente ADVERSA: lo que más se ha movido es «{worst['label']}», "
            f"y hacia abajo respecto a su línea base."
        )
    else:
        direction_summary = "Desviación mixta sin una variable dominante."

    caveats = [
        "El score compara al paciente CONSIGO MISMO, nunca con otros pacientes ni con una norma poblacional.",
        "El compuesto es la media de |z| de las cuatro variables, por lo que es ciego a la dirección: "
        "usa el desglose por variable para saber si el cambio es a mejor o a peor.",
        "Los textos de chat y diario NO entran en este score. Se analizan por separado (Agente 2).",
    ]
    baseline_n = _number(structural.get("baseline_sample_count"))
    recent_n = _number(structural.get("recent_sample_count"))
    if recent_n is not None and recent_n < 3:
        caveats.append(
            f"Solo {int(recent_n)} check-in(s) en la ventana reciente de 7 días: el score es muy sensible "
            f"a cada registro individual."
        )

    return {
        "score": score,
        "band": band,
        "band_label": BAND_LABELS.get(band, band),
        "band_meaning": BAND_MEANING.get(band),
        "scale_note": empty["scale_note"],
        "summary": summary,
        "direction_summary": direction_summary,
        "variables": variables,
        "composite_z": composite_z,
        "adverse_composite_z": adverse_z,
        "favourable_composite_z": favourable_z,
        "baseline_sample_count": int(baseline_n) if baseline_n is not None else None,
        "recent_sample_count": int(recent_n) if recent_n is not None else None,
        "sleep_trend": signals.get("sleep_trend"),
        "sleep_trend_slope": _number(signals.get("sleep_trend_slope")),
        "caveats": caveats,
    }


# --------------------------------------------------- psychosocial view -----
PSYCHOSOCIAL_BAND_LABELS = {
    "alta": "alta",
    "moderada": "moderada",
    "baja": "baja",
    "sin_datos": "sin datos",
}

PSYCHOSOCIAL_SCALE_NOTE = (
    "El índice psicosocial va de 0.00 a 1.00 y resume la ADVERSIDAD del contexto de vida del paciente: "
    "vivienda, convivencia, apoyo social, familia, dinero, ocupación, pérdidas, vínculo con el tratamiento "
    "y entorno de consumo. 0.00 = sin adversidad registrada; 1.00 = adversidad marcada en los dominios de "
    "más peso. A diferencia del score estructural, aquí MÁS ALTO ES PEOR. No es un instrumento validado: es "
    "una media ponderada de lo que el paciente ha contado, con pesos fijos que puedes consultar en el manual."
)


# Each index is rendered with the threshold it is compared against, because a
# number a therapist cannot locate on a scale is a number they cannot argue
# with. "Sin datos" is a first-class reading: it is not the same as "bien".
PSYCHOSOCIAL_INDEX_META: tuple[dict[str, Any], ...] = (
    {
        "key": "support_index",
        "label": "Apoyo disponible",
        "direction": "higher_is_better",
        "threshold": psychosocial_service.SUPPORT_LOW_MAX,
        "threshold_label": "bajo si ≤ {value:.2f}",
        "meaning": (
            "Personas a las que podría recurrir de verdad, ponderando red de apoyo, familia, "
            "vínculos, pareja y estigma."
        ),
        "empty": "Todavía no ha hablado de con quién cuenta. Ausencia de dato, no ausencia de apoyo.",
    },
    {
        "key": "material_adversity_index",
        "label": "Adversidad material",
        "direction": "lower_is_better",
        "threshold": psychosocial_service.MATERIAL_ADVERSITY_HIGH_MIN,
        "threshold_label": "alta si ≥ {value:.2f}",
        "meaning": "Vivienda, dinero, empleo, necesidades básicas, trámites y acceso a tratamiento.",
        "empty": "No ha contado nada de su situación material.",
    },
    {
        "key": "interpersonal_risk_index",
        "label": "Riesgo interpersonal",
        "direction": "lower_is_better",
        "threshold": psychosocial_service.INTERPERSONAL_RISK_HIGH_MIN,
        "threshold_label": "alto si ≥ {value:.2f}",
        "meaning": (
            "Sentirse una carga y no pertenecer, los dos constructos de la teoría interpersonal "
            "del suicidio, más la retirada del contacto. Su convergencia es lo que se vigila."
        ),
        "empty": "No hay material sobre cómo se sitúa respecto a los demás.",
    },
    {
        "key": "relapse_context_index",
        "label": "Contexto de recaída",
        "direction": "lower_is_better",
        "threshold": psychosocial_service.RELAPSE_CONTEXT_HIGH_MIN,
        "threshold_label": "alto si ≥ {value:.2f}",
        "meaning": "Entorno social que sostiene o dispara el consumo, convivencia y estructura diaria.",
        "empty": "No hay material sobre el entorno de consumo.",
    },
)


def _psychosocial_index_readings(indices: dict[str, float | None]) -> list[dict[str, Any]]:
    """Turn the four numbers into something a clinician can read and challenge."""
    readings: list[dict[str, Any]] = []
    for meta in PSYCHOSOCIAL_INDEX_META:
        value = indices.get(meta["key"])
        if value is None:
            state, note = "sin_datos", meta["empty"]
        elif meta["direction"] == "higher_is_better":
            crossed = value <= meta["threshold"]
            state = "alerta" if crossed else "ok"
            note = (
                "Por debajo del umbral de apoyo bajo."
                if crossed
                else "Por encima del umbral de apoyo bajo."
            )
        else:
            crossed = value >= meta["threshold"]
            state = "alerta" if crossed else "ok"
            note = "Cruza el umbral." if crossed else "Por debajo del umbral."
        readings.append(
            {
                "key": meta["key"],
                "label": meta["label"],
                "value": value,
                "state": state,
                "threshold": meta["threshold"],
                "threshold_label": meta["threshold_label"].format(value=meta["threshold"]),
                "meaning": meta["meaning"],
                "note": note,
            }
        )
    return readings


def psychosocial_explanation(
    assessment_snapshot: dict | None,
    live: Any | None = None,
) -> dict[str, Any]:
    """Narrate the psychosocial index for the therapist.

    Prefers the live assessment (which carries the per-domain detail and the
    supporting quotes) and falls back to the snapshot persisted with the risk
    assessment, so a historic decision can still be explained.
    """
    snapshot = _as_dict(assessment_snapshot)

    if live is None:
        index = _number(snapshot.get("index"))
        band = snapshot.get("band") or "sin_datos"
        domains: list[dict[str, Any]] = []
        acute: list[dict[str, Any]] = []
        counts = {
            "observation_count": snapshot.get("observation_count") or 0,
            "active_count": snapshot.get("active_count") or 0,
            "confirmed_count": snapshot.get("confirmed_count") or 0,
            "refuted_count": snapshot.get("refuted_count") or 0,
        }
        snapshot_indices = _as_dict(snapshot.get("indices"))
        indices = {
            "support_index": _number(snapshot_indices.get("support_index")),
            "material_adversity_index": _number(snapshot_indices.get("material_adversity_index")),
            "interpersonal_risk_index": _number(snapshot_indices.get("interpersonal_risk_index")),
            "relapse_context_index": _number(snapshot_indices.get("relapse_context_index")),
        }
        leave_taking = _as_dict(snapshot.get("leave_taking")) or None
        interpersonal_recent = list(snapshot.get("interpersonal_recent_evidence") or [])
        pending_updates = list(snapshot.get("pending_update_domains") or [])
        stale_domains = list(snapshot.get("stale_domains") or [])
        session_questions: list[dict[str, Any]] = []
    else:
        index = live.index
        band = live.band
        domains = [
            {
                "domain": state.domain,
                "label": state.label,
                "category": state.category,
                "category_label": state.category_label,
                "valence": state.valence,
                "intensity": state.intensity,
                "confidence": state.confidence,
                "status": state.status,
                "summary": state.summary,
                "quote": state.quote,
                "observed_at": _utc_iso(state.observed_at),
                "observation_id": str(state.observation_id),
                "weight": state.weight,
                "contribution": state.contribution,
                "is_change": state.is_change,
                "group": state.group,
                "group_label": state.group_label,
                "risk_value": state.risk_value,
                "counts_for_scoring": state.counts_for_scoring,
                "is_stale": state.is_stale,
                "has_pending_update": state.has_pending_update,
                "session_question": state.session_question,
            }
            for state in live.domains
        ]
        acute = [
            {
                "domain": state.domain,
                "label": state.label,
                "category": state.category,
                "category_label": state.category_label,
                "summary": state.summary,
                "quote": state.quote,
                "observed_at": _utc_iso(state.observed_at),
                "observation_id": str(state.observation_id),
            }
            for state in live.acute_changes
        ]
        counts = {
            "observation_count": live.observation_count,
            "active_count": live.active_count,
            "confirmed_count": live.confirmed_count,
            "refuted_count": live.refuted_count,
        }
        indices = {
            "support_index": live.support_index,
            "material_adversity_index": live.material_adversity_index,
            "interpersonal_risk_index": live.interpersonal_risk_index,
            "relapse_context_index": live.relapse_context_index,
        }
        leave_taking = (
            {
                "domain": live.leave_taking.domain,
                "label": live.leave_taking.label,
                "category": live.leave_taking.category,
                "category_label": live.leave_taking.category_label,
                "summary": live.leave_taking.summary,
                "quote": live.leave_taking.quote,
                "observed_at": _utc_iso(live.leave_taking.observed_at),
                "observation_id": str(live.leave_taking.observation_id),
            }
            if live.leave_taking
            else None
        )
        interpersonal_recent = list(live.interpersonal_recent_evidence)
        pending_updates = list(live.pending_update_domains)
        stale_domains = list(live.stale_domains)
        session_questions = psychosocial_service.suggested_session_questions(live)

    if index is None:
        summary = (
            "Todavía no hay contexto psicosocial registrado. El Agente 4 solo extrae lo que el paciente "
            "cuenta espontáneamente en el chat o en el diario; si aún no ha hablado de su situación, no hay "
            "nada que ponderar."
        )
    elif band == "alta":
        summary = f"{index:.2f} · vulnerabilidad alta. Su situación de vida está añadiendo adversidad relevante."
    elif band == "moderada":
        summary = f"{index:.2f} · vulnerabilidad moderada. Hay adversidad contextual identificable."
    else:
        summary = f"{index:.2f} · vulnerabilidad baja según lo que ha contado hasta ahora."

    risk_states = [d for d in domains if d["valence"] == "risk"]
    protective_states = [d for d in domains if d["valence"] == "protective"]
    if risk_states:
        top = risk_states[0]
        driver_summary = (
            f"Lo que más pesa ahora mismo: «{top['label']} — {top['category_label']}» "
            f"(aporta {top['contribution']:.2f} al índice)."
        )
    else:
        driver_summary = "No hay dominios adversos activos registrados."

    if protective_states:
        protective_summary = (
            "Factores protectores registrados: "
            + ", ".join(f"{d['label']} ({d['category_label']})" for d in protective_states[:4])
            + ". Restan hasta un 35 % de la adversidad, nunca la cancelan del todo."
        )
    else:
        protective_summary = (
            "No hay ningún factor protector registrado. Ojo: puede significar que no los tiene, o "
            "simplemente que no ha hablado de ellos."
        )

    caveats = [
        "Todo esto son INFERENCIAS de un modelo de lenguaje sobre lo que el paciente escribió. Cada "
        "observación lleva la frase literal que la sostiene: léela antes de actuar.",
        "Puedes confirmar o refutar cada observación. Confirmada cuenta al 100 % de su intensidad; "
        "refutada deja de contar por completo.",
        "Solo cuenta la observación más reciente de cada dominio. No caducan — perder el piso sigue "
        "siendo cierto la semana que viene — pero se marcan como antiguas a los "
        f"{psychosocial_service.STALE_AFTER_DAYS} días.",
        f"Una observación con confianza por debajo de {psychosocial_service.MIN_CONFIDENCE_FOR_SCORING:.2f} se muestra pero no puntúa: "
        "una mención de pasada o irónica no debe mover un umbral.",
        "El índice por sí solo nunca genera alerta profesional: para llegar a nivel 3 tiene que converger "
        "con inestabilidad estructural, sueño empeorando o rumiación alta.",
        "Los pesos por dominio son un criterio de diseño explícito, no un instrumento psicométrico validado.",
    ]
    index_readings = _psychosocial_index_readings(indices)
    if leave_taking:
        caveats.insert(
            0,
            "Hay una señal de despedida registrada en los últimos 14 días. Mírala antes que ningún número.",
        )
    if pending_updates:
        caveats.append(
            "Hay "
            + str(len(pending_updates))
            + " dominio(s) confirmados por un profesional sobre los que el Agente 4 ha leído algo "
            "distinto después. La lectura nueva NO se ha aplicado: revísala y acéptala o descártala."
        )
    if stale_domains:
        caveats.append(
            "Dominios sin novedades desde hace más de "
            + str(psychosocial_service.STALE_AFTER_DAYS)
            + " días: "
            + ", ".join(stale_domains)
            + ". Siguen siendo lo último que se sabe, pero conviene preguntar si siguen igual."
        )
    if index is not None and counts["active_count"] < 3:
        caveats.append(
            f"Solo {counts['active_count']} dominio(s) activo(s): el índice es muy sensible a cada nueva "
            f"observación y puede moverse mucho con una sola frase."
        )

    return {
        "index": index,
        "band": band,
        "band_label": PSYCHOSOCIAL_BAND_LABELS.get(band, band),
        "scale_note": PSYCHOSOCIAL_SCALE_NOTE,
        "summary": summary,
        "driver_summary": driver_summary,
        "protective_summary": protective_summary,
        "domains": domains,
        "acute_changes": acute,
        "has_acute_change": bool(acute),
        "acute_note": (
            "Cambios adversos en los últimos 14 días. Son las señales «aparentemente inocuas» que suelen "
            "adelantarse a una crisis o a una recaída: una mudanza, una ruptura, una ayuda que se pierde, "
            "un grupo que se deja, una cita a la que se falta."
        ),
        "caveats": caveats,
        "indices": indices,
        "index_readings": index_readings,
        "leave_taking": leave_taking,
        "leave_taking_note": (
            "Señal de despedida vigente. Cada marcador por separado es inofensivo — regalar algo, dar "
            "las gracias, ordenar papeles, una calma repentina —; se registra precisamente porque "
            "juntos, y junto a otras señales, dejan de serlo. No es una conclusión: es una pregunta "
            "que hacer en la próxima sesión."
        ),
        "interpersonal_recent_evidence": interpersonal_recent,
        "pending_update_domains": pending_updates,
        "stale_domains": stale_domains,
        "session_questions": session_questions,
        **counts,
    }


# ------------------------------------------------------------- evidence ----
def _source_for_trace(
    trace: Agent2AnalysisTrace,
    chat_by_id: dict[uuid.UUID, ChatMessage],
    diary_by_id: dict[uuid.UUID, DiaryEntry],
) -> tuple[uuid.UUID | None, str, datetime | None]:
    if trace.source_type == "chat_message":
        row = chat_by_id.get(trace.chat_message_id)
        source_id = trace.chat_message_id
    else:
        row = diary_by_id.get(trace.diary_entry_id)
        source_id = trace.diary_entry_id
    if row is None or row.user_id != trace.user_id:
        return source_id, "", None
    return source_id, row.content, row.created_at


def build_evidence_feed(db: Session, patient_id, limit: int = 60) -> list[dict[str, Any]]:
    """One row per analysed text: what the patient wrote, what the model read
    in it, and what the deterministic engine did with it.

    This is the answer to "the alerts do not reference the text that caused
    them": every item carries the source excerpt, its origin (chat or diary),
    the Agent 2 output, the resulting level and the alert id when one was
    created.
    """
    traces = (
        db.query(Agent2AnalysisTrace)
        .filter(
            Agent2AnalysisTrace.user_id == patient_id,
            # Agent 4 shares this lineage table but produces psychosocial
            # observations, not linguistic signals; it has its own view.
            Agent2AnalysisTrace.agent_role == "agent2_linguistic",
        )
        .order_by(Agent2AnalysisTrace.started_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    if not traces:
        return []

    trace_ids = [t.id for t in traces]
    chat_ids = [t.chat_message_id for t in traces if t.chat_message_id]
    diary_ids = [t.diary_entry_id for t in traces if t.diary_entry_id]

    chat_by_id = {
        row.id: row
        for row in (db.query(ChatMessage).filter(ChatMessage.id.in_(chat_ids)).all() if chat_ids else [])
    }
    diary_by_id = {
        row.id: row
        for row in (db.query(DiaryEntry).filter(DiaryEntry.id.in_(diary_ids)).all() if diary_ids else [])
    }

    signals = (
        db.query(AlfaSignal)
        .filter(AlfaSignal.user_id == patient_id, AlfaSignal.agent2_trace_id.in_(trace_ids))
        .order_by(AlfaSignal.timestamp.desc())
        .all()
    )
    signal_by_trace: dict[uuid.UUID, AlfaSignal] = {}
    for signal in signals:
        signal_by_trace.setdefault(signal.agent2_trace_id, signal)

    assessments = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient_id, RiskAssessment.agent2_trace_id.in_(trace_ids))
        .order_by(RiskAssessment.calculated_at.desc())
        .all()
    )
    assessment_by_trace: dict[uuid.UUID, RiskAssessment] = {}
    for assessment in assessments:
        assessment_by_trace.setdefault(assessment.agent2_trace_id, assessment)

    alert_ids = [a.generated_alert_id for a in assessments if a.generated_alert_id]
    alert_by_id = {
        row.id: row
        for row in (
            db.query(ProfessionalAlert).filter(ProfessionalAlert.id.in_(alert_ids)).all() if alert_ids else []
        )
    }

    feed: list[dict[str, Any]] = []
    for trace in traces:
        source_id, source_text, source_created_at = _source_for_trace(trace, chat_by_id, diary_by_id)
        signal = signal_by_trace.get(trace.id)
        assessment = assessment_by_trace.get(trace.id)
        alert = alert_by_id.get(assessment.generated_alert_id) if assessment and assessment.generated_alert_id else None
        analysis = _as_dict(signal.value) if signal else None

        flags: list[str] = []
        if analysis:
            if analysis.get("ideation_direct"):
                flags.append("ideación directa")
            if analysis.get("ideation_indirect"):
                flags.append("ideación indirecta")
            if analysis.get("consumption_crisis"):
                flags.append("crisis de consumo")

        if trace.status != "succeeded":
            reading = (
                "El análisis no llegó a completarse "
                f"({trace.status}). El motor determinista siguió funcionando sin señal lingüística "
                "para este texto."
            )
        elif not analysis:
            reading = "Análisis completado pero sin señal guardada."
        elif flags:
            reading = "El Agente 2 marcó: " + ", ".join(flags) + "."
        else:
            reading = "El Agente 2 no marcó ninguna bandera crítica en este texto."

        feed.append(
            {
                "trace_id": str(trace.id),
                "correlation_id": str(trace.correlation_id),
                "source_type": trace.source_type,
                "source_label": "Chat" if trace.source_type == "chat_message" else "Diario",
                "source_id": str(source_id) if source_id else None,
                "source_text": source_text,
                "source_excerpt": _excerpt(source_text),
                "source_created_at": _utc_iso(source_created_at),
                "analysed_at": _utc_iso(trace.started_at),
                "status": trace.status,
                "analysis": analysis,
                "flags": flags,
                "reading": reading,
                "short_rationale": (analysis or {}).get("short_rationale"),
                "signal_id": str(signal.id) if signal else None,
                "assessment_id": str(assessment.id) if assessment else None,
                "resulting_level": assessment.alert_level if assessment else None,
                "resulting_rule": selected_rule_code(assessment) if assessment else None,
                "used_by_risk_engine": bool(
                    signal and assessment and assessment.linguistic_signal_id_used == signal.id
                ),
                "alert_id": str(alert.id) if alert else None,
                "alert_level": alert.alert_level if alert else None,
                "alert_status": alert.status if alert else None,
                "alert_title": alert.title if alert else None,
            }
        )
    return feed


def evidence_for_assessment(db: Session, assessment: RiskAssessment | None) -> dict[str, Any] | None:
    """Resolve the concrete piece of evidence behind one decision.

    For a linguistic rule this is the exact chat message or diary entry the
    model read. For a confirmed-fact rule it is the declaration itself.
    """
    if assessment is None:
        return None
    code = selected_rule_code(assessment)
    family = rule_info(code)["family"]

    if family == FAMILY_LINGUISTIC and assessment.agent2_trace_id:
        trace = db.get(Agent2AnalysisTrace, assessment.agent2_trace_id)
        if trace and trace.user_id == assessment.user_id:
            source_model = ChatMessage if trace.source_type == "chat_message" else DiaryEntry
            source_id = trace.chat_message_id or trace.diary_entry_id
            row = db.get(source_model, source_id) if source_id else None
            content = row.content if row is not None and row.user_id == trace.user_id else ""
            signal = (
                db.query(AlfaSignal)
                .filter(AlfaSignal.id == assessment.linguistic_signal_id_used)
                .first()
                if assessment.linguistic_signal_id_used
                else None
            )
            return {
                "kind": "texto",
                "source_type": trace.source_type,
                "source_label": "Chat" if trace.source_type == "chat_message" else "Diario",
                "source_id": str(source_id) if source_id else None,
                "text": content,
                "excerpt": _excerpt(content),
                "created_at": _utc_iso(getattr(row, "created_at", None)),
                "analysis": _as_dict(signal.value) if signal else None,
                "trace_id": str(trace.id),
                "signal_id": str(signal.id) if signal else None,
            }

    if family == FAMILY_CONFIRMED_FACT:
        facts = _as_dict(assessment.input_facts)
        declared = _as_list(facts.get("n4_declarations")) + _as_list(facts.get("n3_declarations"))
        fact_ids = [row.get("id") for row in declared if isinstance(row, dict) and row.get("id")]
        rows = (
            db.query(ConfirmedFact)
            .filter(ConfirmedFact.user_id == assessment.user_id, ConfirmedFact.id.in_(fact_ids))
            .order_by(ConfirmedFact.created_at.desc())
            .all()
            if fact_ids
            else []
        )
        if rows:
            row = rows[0]
            return {
                "kind": "hecho",
                "source_type": "confirmed_fact",
                "source_label": "Hecho confirmado",
                "source_id": str(row.id),
                "text": row.content,
                "excerpt": _excerpt(row.content),
                "created_at": _utc_iso(row.created_at),
                "category": row.category,
                "declared_by": row.declared_by,
            }

    if family == FAMILY_PSYCHOSOCIAL:
        # Prefer the acute change that actually triggered the rule; fall back
        # to the heaviest adverse domain for the convergence rule.
        psycho_trace = _as_dict(_as_dict(trace_inputs(assessment)).get("psychosocial"))
        domain_rows = [row for row in _as_list(psycho_trace.get("domains")) if isinstance(row, dict)]
        contribution_by_id = {
            row.get("observation_id"): _number(row.get("contribution")) or 0.0 for row in domain_rows
        }
        candidates = _as_list(psycho_trace.get("acute_changes")) or [
            row for row in domain_rows if row.get("valence") == "risk"
        ]
        # Lead with the heaviest contributor, not with whichever the model
        # happened to list first: one message frequently produces several
        # observations sharing the same timestamp.
        observation_ids = [
            row.get("observation_id")
            for row in sorted(
                (row for row in candidates if isinstance(row, dict)),
                key=lambda row: contribution_by_id.get(row.get("observation_id"), 0.0),
                reverse=True,
            )
        ]
        by_id = {
            row.id: row
            for row in (
                db.query(PsychosocialObservation)
                .filter(
                    PsychosocialObservation.user_id == assessment.user_id,
                    PsychosocialObservation.id.in_(observation_ids),
                )
                .all()
                if observation_ids
                else []
            )
        }
        rows = [by_id[uuid.UUID(str(oid))] for oid in observation_ids if uuid.UUID(str(oid)) in by_id]
        if rows:
            row = rows[0]
            return {
                "kind": "psicosocial",
                "source_type": row.source_type,
                "source_label": "Chat" if row.source_type == "chat_message" else "Diario",
                "source_id": str(row.chat_message_id or row.diary_entry_id),
                "text": row.evidence_quote,
                "excerpt": _excerpt(row.evidence_quote),
                "created_at": _utc_iso(row.observed_at),
                "category": psychosocial_service.CATEGORY_LABELS.get(row.category, row.category),
                "domain_label": psychosocial_service.DOMAIN_LABELS.get(row.domain, row.domain),
                "summary": row.summary,
                "status": row.status,
                "observation_id": str(row.id),
            }
        return {
            "kind": "psicosocial",
            "source_type": "psychosocial",
            "source_label": "Contexto psicosocial",
            "source_id": None,
            "text": "",
            "excerpt": "",
            "created_at": _utc_iso(assessment.calculated_at),
        }

    if family in (FAMILY_STRUCTURAL, FAMILY_CONVERGENCE):
        return {
            "kind": "estructural",
            "source_type": "check_ins",
            "source_label": "Check-ins diarios",
            "source_id": None,
            "text": "",
            "excerpt": "",
            "created_at": _utc_iso(assessment.calculated_at),
        }
    return None


# -------------------------------------------------------------- metrics ----
def build_metrics(db: Session, patient_id, window_days: int = 90) -> dict[str, Any]:
    """Chart-ready series for the professional panel.

    Everything is returned as flat, already-sorted arrays with ISO
    timestamps so the frontend only has to draw it.
    """
    since = datetime.utcnow() - timedelta(days=window_days)

    checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == patient_id, CheckIn.created_at >= since)
        .order_by(CheckIn.created_at.asc())
        .all()
    )
    structural_signals = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.user_id == patient_id,
            AlfaSignal.signal_type == "structural_score",
            AlfaSignal.timestamp >= since,
        )
        .order_by(AlfaSignal.timestamp.asc())
        .all()
    )
    linguistic_signals = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.user_id == patient_id,
            AlfaSignal.signal_type == "linguistic_analysis",
            AlfaSignal.timestamp >= since,
        )
        .order_by(AlfaSignal.timestamp.asc())
        .all()
    )
    assessments = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.user_id == patient_id, RiskAssessment.calculated_at >= since)
        .order_by(RiskAssessment.calculated_at.asc())
        .all()
    )
    alerts = (
        db.query(ProfessionalAlert)
        .filter(ProfessionalAlert.user_id == patient_id, ProfessionalAlert.created_at >= since)
        .order_by(ProfessionalAlert.created_at.asc())
        .all()
    )
    facts = (
        db.query(ConfirmedFact)
        .filter(ConfirmedFact.user_id == patient_id, ConfirmedFact.created_at >= since)
        .order_by(ConfirmedFact.created_at.asc())
        .all()
    )

    # Map linguistic signals back to the text they came from, so a spike in
    # the chart can be clicked through to the sentence that produced it.
    ling_trace_ids = [s.agent2_trace_id for s in linguistic_signals if s.agent2_trace_id]
    traces_by_id = {
        row.id: row
        for row in (
            db.query(Agent2AnalysisTrace).filter(Agent2AnalysisTrace.id.in_(ling_trace_ids)).all()
            if ling_trace_ids
            else []
        )
    }
    chat_ids = [t.chat_message_id for t in traces_by_id.values() if t.chat_message_id]
    diary_ids = [t.diary_entry_id for t in traces_by_id.values() if t.diary_entry_id]
    chat_by_id = {
        row.id: row
        for row in (db.query(ChatMessage).filter(ChatMessage.id.in_(chat_ids)).all() if chat_ids else [])
    }
    diary_by_id = {
        row.id: row
        for row in (db.query(DiaryEntry).filter(DiaryEntry.id.in_(diary_ids)).all() if diary_ids else [])
    }

    checkin_series = [
        {
            "at": _utc_iso(row.created_at),
            "date": row.created_at.strftime("%Y-%m-%d"),
            "mood": row.mood,
            "craving": row.craving,
            "sleep_hours": row.sleep_hours,
            "self_efficacy": row.self_efficacy,
            "notes": row.notes,
        }
        for row in checkins
    ]

    structural_series = []
    for row in structural_signals:
        value = _as_dict(row.value)
        z_scores = _as_dict(value.get("z_scores"))
        structural_series.append(
            {
                "at": _utc_iso(row.timestamp),
                "date": row.timestamp.strftime("%Y-%m-%d"),
                "score": _number(value.get("score")),
                "band": row.confidence_band,
                "composite_z": _number(value.get("composite_z")),
                "z_mood": _number(z_scores.get("mood")),
                "z_craving_inv": _number(z_scores.get("craving_inv")),
                "z_sleep_hours": _number(z_scores.get("sleep_hours")),
                "z_self_efficacy": _number(z_scores.get("self_efficacy")),
            }
        )

    # The structural score is recomputed on every risk run, so a talkative day
    # produces a dozen identical points. Collapse to one point per day (the
    # last of that day) for the chart; the raw series stays available.
    daily_structural: dict[str, dict[str, Any]] = {}
    for point in structural_series:
        daily_structural[point["date"]] = point
    daily_structural_series = [daily_structural[day] for day in sorted(daily_structural)]

    linguistic_series = []
    for row in linguistic_signals:
        value = _as_dict(row.value)
        trace = traces_by_id.get(row.agent2_trace_id) if row.agent2_trace_id else None
        source_text = ""
        source_type = None
        source_id = None
        if trace is not None:
            source_type = trace.source_type
            source_id, source_text, _created = _source_for_trace(trace, chat_by_id, diary_by_id)
        linguistic_series.append(
            {
                "at": _utc_iso(row.timestamp),
                "date": row.timestamp.strftime("%Y-%m-%d"),
                "signal_id": str(row.id),
                "rumination_score": _number(value.get("rumination_score")),
                "negative_valence": _number(value.get("negative_valence")),
                "urgency_level": _number(value.get("urgency_level")),
                "ambivalence": _number(value.get("ambivalence")),
                "ideation_direct": bool(value.get("ideation_direct")),
                "ideation_indirect": bool(value.get("ideation_indirect")),
                "consumption_crisis": bool(value.get("consumption_crisis")),
                "emotional_complexity": value.get("emotional_complexity"),
                "short_rationale": value.get("short_rationale"),
                "source_type": source_type,
                "source_label": None
                if source_type is None
                else ("Chat" if source_type == "chat_message" else "Diario"),
                "source_id": str(source_id) if source_id else None,
                "source_excerpt": _excerpt(source_text, 200),
                "trace_id": str(trace.id) if trace else None,
            }
        )

    # Psychosocial index over time. It is only ever computed inside a risk
    # evaluation, so the assessment history *is* its history — no separate
    # series to keep in sync.
    psychosocial_series = []
    for row in assessments:
        snapshot = _as_dict(_as_dict(row.input_signals).get("psychosocial"))
        index = _number(snapshot.get("index"))
        if index is None:
            continue
        psychosocial_series.append(
            {
                "at": _utc_iso(row.calculated_at),
                "date": row.calculated_at.strftime("%Y-%m-%d"),
                "index": index,
                "band": snapshot.get("band"),
                "has_acute_change": bool(snapshot.get("has_acute_change")),
                "active_count": snapshot.get("active_count"),
                "assessment_id": str(row.id),
            }
        )
    daily_psychosocial: dict[str, dict[str, Any]] = {}
    for point in psychosocial_series:
        daily_psychosocial[point["date"]] = point
    daily_psychosocial_series = [daily_psychosocial[day] for day in sorted(daily_psychosocial)]

    observations = (
        db.query(PsychosocialObservation)
        .filter(
            PsychosocialObservation.user_id == patient_id,
            PsychosocialObservation.observed_at >= since,
        )
        .order_by(PsychosocialObservation.observed_at.asc())
        .all()
    )
    psychosocial_events = [
        {
            "at": _utc_iso(row.observed_at),
            "date": row.observed_at.strftime("%Y-%m-%d"),
            "domain": row.domain,
            "domain_label": psychosocial_service.DOMAIN_LABELS.get(row.domain, row.domain),
            "category": row.category,
            "category_label": psychosocial_service.CATEGORY_LABELS.get(row.category, row.category),
            "valence": row.valence,
            "intensity": float(row.intensity),
            "confidence": float(row.confidence),
            "is_change": bool(row.is_change),
            "status": row.status,
            "summary": row.summary,
            "quote": row.evidence_quote,
            "source_label": "Chat" if row.source_type == "chat_message" else "Diario",
            "observation_id": str(row.id),
        }
        for row in observations
    ]

    level_series = [
        {
            "at": _utc_iso(row.calculated_at),
            "date": row.calculated_at.strftime("%Y-%m-%d"),
            "level": row.alert_level,
            "assessment_id": str(row.id),
            "rule": selected_rule_code(row),
            "rule_family": rule_info(selected_rule_code(row))["family"],
            "reason": row.assessment_reason,
            "generated_alert_id": str(row.generated_alert_id) if row.generated_alert_id else None,
        }
        for row in assessments
    ]

    # Daily max level: the per-assessment series is spiky (one point per
    # chat turn), which is unreadable over 90 days.
    by_day: dict[str, int] = defaultdict(int)
    for row in assessments:
        day = row.calculated_at.strftime("%Y-%m-%d")
        by_day[day] = max(by_day[day], row.alert_level)
    daily_level_series = [{"date": day, "max_level": level} for day, level in sorted(by_day.items())]

    events = [
        {
            "at": _utc_iso(row.created_at),
            "date": row.created_at.strftime("%Y-%m-%d"),
            "kind": "alert",
            "level": row.alert_level,
            "label": row.title,
            "status": row.status,
            "id": str(row.id),
        }
        for row in alerts
    ] + [
        {
            "at": _utc_iso(row.created_at),
            "date": row.created_at.strftime("%Y-%m-%d"),
            "kind": "fact",
            "level": None,
            "label": f"Hecho: {row.category} ({row.declared_by})",
            "status": "active" if row.is_active else "superseded",
            "id": str(row.id),
        }
        for row in facts
    ]
    events.sort(key=lambda item: item["at"] or "")

    return {
        "window_days": window_days,
        "generated_at": _utc_iso(datetime.utcnow()),
        "checkins": checkin_series,
        "structural": structural_series,
        "daily_structural": daily_structural_series,
        "psychosocial": psychosocial_series,
        "daily_psychosocial": daily_psychosocial_series,
        "psychosocial_events": psychosocial_events,
        "linguistic": linguistic_series,
        "levels": level_series,
        "daily_levels": daily_level_series,
        "events": events,
        "counts": {
            "checkins": len(checkin_series),
            "structural_points": len(structural_series),
            "linguistic_points": len(linguistic_series),
            "psychosocial_points": len(psychosocial_series),
            "psychosocial_observations": len(psychosocial_events),
            "assessments": len(level_series),
            "alerts": len(alerts),
            "facts": len(facts),
        },
    }
