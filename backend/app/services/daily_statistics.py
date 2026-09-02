"""Descriptive daily statistics, never a diagnosis or a risk-level input.

Check-ins and analysed patient texts are different sampling units. Average
within each local calendar day first, then give each observed day one vote
in the window summary and pairwise correlations. Missing is never zero.
No raw text, patient identifiers, or cross-patient aggregates leave here.
"""
from __future__ import annotations

import math
import uuid
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from itertools import combinations
from statistics import fmean, stdev
from typing import Any, Iterable
from zoneinfo import ZoneInfo

TIMEZONE = "Europe/Madrid"
LOCAL_ZONE = ZoneInfo(TIMEZONE)
CHECKIN_KEYS = ("mood", "craving", "sleep_hours", "self_efficacy")
TEXT_NUMERIC_KEYS = ("rumination_score", "negative_valence", "urgency_level", "ambivalence")
TEXT_BOOLEAN_KEYS = ("ideation_direct", "ideation_indirect", "consumption_crisis", "is_typical_for_patient", "has_psychosocial_content")
TEXT_CATEGORY_KEYS = ("emotional_complexity", "deviation_from_own_baseline")


def _variable(key: str, label: str, kind: str, source: str, unit: str = "") -> dict:
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "source": source,
        "unit": unit,
        "aggregation": "mean" if kind == "numeric" else "any" if kind == "boolean" else "counts",
    }


VARIABLES = [
    _variable("mood", "Ánimo", "numeric", "checkin", "0–10"),
    _variable("sleep_hours", "Horas de sueño", "numeric", "checkin", "h"),
    _variable("self_efficacy", "Autoeficacia", "numeric", "checkin", "0–10"),
    _variable("craving", "Craving", "numeric", "checkin", "0–10"),
    _variable("interaction_valence_mean", "Valencia negativa media de interacciones", "numeric", "linguistic", "0–1"),
    _variable("rumination_score", "Rumiación", "numeric", "linguistic", "0–1"),
    _variable("negative_valence", "Valencia negativa (variable original)", "numeric", "linguistic", "0–1"),
    _variable("urgency_level", "Urgencia del malestar", "numeric", "linguistic", "0–1"),
    _variable("ambivalence", "Ambivalencia", "numeric", "linguistic", "0–1"),
    _variable("ideation", "Alguna señal textual de ideación", "boolean", "linguistic"),
    _variable("ideation_direct", "Ideación directa inferida", "boolean", "linguistic"),
    _variable("ideation_indirect", "Ideación indirecta inferida", "boolean", "linguistic"),
    _variable("consumption_crisis", "Crisis de consumo inferida", "boolean", "linguistic"),
    _variable("is_typical_for_patient", "Algún texto habitual para la persona", "boolean", "linguistic"),
    _variable("has_psychosocial_content", "Contenido psicosocial inferido en algún texto", "boolean", "linguistic"),
    _variable("emotional_complexity", "Complejidad emocional", "categorical", "linguistic"),
    _variable("deviation_from_own_baseline", "Desviación de su expresión habitual", "categorical", "linguistic"),
    _variable("psychosocial_intensity_mean", "Intensidad psicosocial", "numeric", "psychosocial", "0–1"),
    _variable("psychosocial_confidence_mean", "Confianza de la extracción psicosocial", "numeric", "psychosocial", "0–1"),
    _variable("psychosocial_is_change", "Algún cambio psicosocial", "boolean", "psychosocial"),
    _variable("psychosocial_domain", "Dominio psicosocial", "categorical", "psychosocial"),
    _variable("psychosocial_category", "Categoría psicosocial", "categorical", "psychosocial"),
    _variable("psychosocial_valence", "Valencia psicosocial", "categorical", "psychosocial"),
    _variable("psychosocial_status", "Estado de la observación psicosocial", "categorical", "psychosocial"),
]


def utc_datetime(value: datetime) -> datetime:
    """Existing timestamp-without-timezone columns are UTC by app contract."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def local_day(value: datetime) -> str:
    return utc_datetime(value).astimezone(LOCAL_ZONE).date().isoformat()


def window_bounds(window_days: int, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Inclusive first local midnight, through now; UTC-naive DB boundaries."""
    now = utc_datetime(now or datetime.now(timezone.utc))
    window_days = max(1, min(int(window_days), 365))
    first_date = now.astimezone(LOCAL_ZONE).date() - timedelta(days=window_days - 1)
    start = datetime.combine(first_date, time.min, LOCAL_ZONE).astimezone(timezone.utc)
    return start.replace(tzinfo=None), now.replace(tzinfo=None)


def _numeric(value: Any, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 <= value <= maximum else None


def _numeric_stats(values: list[float]) -> dict:
    n = len(values)
    return {
        "n": n,
        "mean": fmean(values) if n else None,
        "sd": stdev(values) if n > 1 else None,
        "min": min(values) if n else None,
        "max": max(values) if n else None,
    }


def _boolean_stats(values: list[bool]) -> dict:
    n = len(values)
    positives = sum(values)
    return {
        "n": n,
        "true_count": positives,
        "false_count": n - positives,
        "rate": positives / n if n else None,
    }


def _category_stats(values: list[str]) -> dict:
    return {"n": len(values), "counts": dict(sorted(Counter(values).items()))}


def _source_key(record: Any) -> tuple[str, str] | None:
    source_type = getattr(record, "source_type", None)
    key = "chat_message_id" if source_type == "chat_message" else "diary_entry_id"
    source_id = getattr(record, key, None)
    return (source_type, str(source_id)) if source_type in ("chat_message", "diary_entry") and source_id else None


def _ideation(value: dict) -> bool | None:
    direct, indirect = value.get("ideation_direct"), value.get("ideation_indirect")
    if direct is True or indirect is True:
        return True
    # One missing component cannot establish that no ideation was detected.
    return False if direct is False and indirect is False else None


def _correlations(daily: list[dict]) -> list[dict]:
    # negative_valence is an exact alias, not an independent variable.
    keys = [v["key"] for v in VARIABLES if v["kind"] != "categorical" and v["key"] != "negative_valence"]
    result = []
    for x, y in combinations(keys, 2):
        pairs = [(float(row[x]), float(row[y])) for row in daily if row.get(x) is not None and row.get(y) is not None]
        n = len(pairs)
        r, status = None, "insufficient_pairs"
        if n >= 3:
            xs, ys = zip(*pairs)
            xm, ym = fmean(xs), fmean(ys)
            sxx = math.fsum((v - xm) ** 2 for v in xs)
            syy = math.fsum((v - ym) ** 2 for v in ys)
            if sxx > 0 and syy > 0:
                r = math.fsum((a - xm) * (b - ym) for a, b in pairs) / math.sqrt(sxx * syy)
                r, status = max(-1.0, min(1.0, r)), "ok"
            else:
                status = "constant_series"
        result.append({"x": x, "y": y, "n": n, "r": r, "status": status, "method": "pearson"})
    return result


def aggregate_daily_statistics(
    checkins: Iterable[Any],
    linguistic_signals: Iterable[Any],
    observations: Iterable[Any] = (),
    *,
    traces_by_id: dict | None = None,
    source_times: dict[tuple[str, str], datetime] | None = None,
    strict_sources: bool = False,
    window_days: int = 30,
    now: datetime | None = None,
) -> dict:
    """Pure calculation over records already scoped to exactly one patient."""
    window_days = max(1, min(int(window_days), 365))
    start, end = window_bounds(window_days, now)
    traces_by_id, source_times = traces_by_id or {}, source_times or {}
    values: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    counts: dict[str, Counter] = defaultdict(Counter)
    provenance = Counter()

    def eligible(at: datetime | None) -> bool:
        return at is not None and start <= utc_datetime(at).replace(tzinfo=None) <= end

    def add(day: str, key: str, value: Any) -> None:
        if value is not None:
            values[day][key].append(value)

    for row in checkins:
        if not eligible(row.created_at):
            continue
        day = local_day(row.created_at)
        counts[day]["checkins"] += 1
        for key in CHECKIN_KEYS:
            add(day, key, _numeric(getattr(row, key, None), 24 if key == "sleep_hours" else 10))

    # A retry/reanalysis of the same interaction must not give that text
    # multiple votes. Choose its last result *before* excluding refutations.
    latest: dict[tuple[str, str], tuple[Any, datetime, bool]] = {}
    for row in sorted(linguistic_signals, key=lambda r: (utc_datetime(r.timestamp), str(r.id))):
        if not eligible(row.timestamp):
            continue
        trace_id = getattr(row, "agent2_trace_id", None)
        trace = traces_by_id.get(trace_id) or traces_by_id.get(str(trace_id))
        source_key = _source_key(trace) if trace is not None else None
        source_at = source_times.get(source_key) if source_key else None
        if strict_sources and trace_id is not None and (trace is None or source_at is None):
            provenance["excluded_unverified_source_signals"] += 1
            continue
        at = source_at or row.timestamp
        if not eligible(at):
            continue
        identity = source_key or ("signal", str(row.id))
        if identity in latest:
            provenance["excluded_duplicate_analyses"] += 1
        latest[identity] = (row, at, source_at is None)

    for row, at, used_fallback in latest.values():
        if getattr(row, "is_active", True) is False or getattr(row, "superseded_by_fact", None):
            provenance["excluded_refuted_signals"] += 1
            continue
        day = local_day(at)
        counts[day]["interactions"] += 1
        counts[day]["interaction_timestamp_fallbacks"] += int(used_fallback)
        provenance["interaction_timestamp_fallbacks"] += int(used_fallback)
        value = row.value if isinstance(row.value, dict) else {}
        for key in TEXT_NUMERIC_KEYS:
            add(day, key, _numeric(value.get(key), 1))
        add(day, "interaction_valence_mean", _numeric(value.get("negative_valence"), 1))
        for key in TEXT_BOOLEAN_KEYS:
            add(day, key, value.get(key) if isinstance(value.get(key), bool) else None)
        add(day, "ideation", _ideation(value))
        for key in TEXT_CATEGORY_KEYS:
            item = value.get(key)
            add(day, key, item if isinstance(item, str) and item else None)

    # Historical observations describe the text at that time. Refuted
    # observations are excluded; a later context change does not erase them.
    latest_observations = {}
    for row in sorted(observations, key=lambda r: (utc_datetime(getattr(r, "created_at", None) or r.observed_at), str(r.id))):
        # The extractor enforces one observation per domain per text. Keep
        # that sampling unit when an extraction was retried as well.
        source_key = _source_key(row)
        identity = (*source_key, row.domain) if source_key else ("observation", str(row.id))
        if identity in latest_observations:
            provenance["excluded_duplicate_observations"] += 1
        latest_observations[identity] = row
    for row in latest_observations.values():
        source_at = source_times.get(_source_key(row))
        if strict_sources and source_at is None:
            provenance["excluded_unverified_source_observations"] += 1
            continue
        at = source_at or row.observed_at
        if not eligible(at):
            continue
        if row.status == "refuted":
            provenance["excluded_refuted_observations"] += 1
            continue
        day = local_day(at)
        counts[day]["psychosocial_observations"] += 1
        add(day, "psychosocial_intensity_mean", _numeric(row.intensity, 1))
        add(day, "psychosocial_confidence_mean", _numeric(row.confidence, 1))
        add(day, "psychosocial_is_change", row.is_change if isinstance(row.is_change, bool) else None)
        for key in ("domain", "category", "valence", "status"):
            item = getattr(row, key, None)
            add(day, f"psychosocial_{key}", item if isinstance(item, str) and item else None)

    daily = []
    for day in sorted(counts):
        row = {"date": day, "statistics": {}, "categories": {}, "counts": {
            key: counts[day][key] for key in ("checkins", "interactions", "psychosocial_observations", "interaction_timestamp_fallbacks")
        }}
        for variable in VARIABLES:
            key, kind = variable["key"], variable["kind"]
            samples = values[day][key]
            if kind == "numeric":
                stats = _numeric_stats(samples)
                row[key] = stats["mean"]
            elif kind == "boolean":
                stats = _boolean_stats(samples)
                row[key] = any(samples) if samples else None
            else:
                stats = _category_stats(samples)
                row["categories"][key] = stats
            denominator = row["counts"][{"checkin": "checkins", "linguistic": "interactions", "psychosocial": "psychosocial_observations"}[variable["source"]]]
            row["statistics"][key] = {**stats, "missing_count": denominator - stats["n"]}
        daily.append(row)

    summary = {}
    for variable in VARIABLES:
        key, kind = variable["key"], variable["kind"]
        if kind == "categorical":
            frequencies, day_counts = Counter(), Counter()
            observed_days = 0
            for day in daily:
                category = day["categories"][key]
                frequencies.update(category["counts"])
                day_counts.update(category["counts"].keys())
                observed_days += int(category["n"] > 0)
            summary[key] = {"n": sum(frequencies.values()), "counts": dict(sorted(frequencies.items())), "day_counts": dict(sorted(day_counts.items())), "observed_days": observed_days, "missing_days": window_days - observed_days}
        else:
            samples = [day[key] for day in daily if day[key] is not None]
            stats = _numeric_stats(samples) if kind == "numeric" else _boolean_stats(samples)
            summary[key] = {**stats, "missing_days": window_days - len(samples)}

    return {
        "version": "daily-statistics-v1",
        "timezone": TIMEZONE,
        "window_days": window_days,
        "start_date": local_day(start),
        "end_date": local_day(end),
        "generated_at": utc_datetime(end).isoformat(),
        "daily": daily,
        "variables": VARIABLES,
        "summary": summary,
        "correlations": _correlations(daily),
        "provenance": {key: provenance[key] for key in ("excluded_duplicate_analyses", "excluded_duplicate_observations", "excluded_refuted_signals", "excluded_refuted_observations", "excluded_unverified_source_signals", "excluded_unverified_source_observations", "interaction_timestamp_fallbacks")},
        "notes": [
            "Días naturales de Europe/Madrid, incluido el día actual parcial. Los timestamps sin zona se interpretan como UTC.",
            "Cada check-in y cada texto analizado tienen un voto dentro de su día; cada día observado tiene el mismo peso en las medias del periodo y las correlaciones. Psicosocial usa una observación por dominio y texto. No se rellenan huecos.",
            "La valencia disponible es negative_valence: 0–1, mayor significa mayor carga negativa. interaction_valence_mean es su media diaria, no una valencia positiva ni una escala clínica validada.",
            "Ideación y otros booleanos diarios indican si hubo al menos una señal; nunca se diluyen por promediar textos. La tasa describe frecuencia de detección, no probabilidad de riesgo.",
            "n indica observaciones en el detalle diario, días en el resumen numérico/booleano y observaciones en categorías. DE es muestral (n−1); con menos de 2 datos no se calcula.",
            "Pearson usa pares de días completos, al menos 3 y variación en ambas series. Booleanos se codifican 0/1; las categorías no se codifican. Correlación exploratoria no implica causalidad ni predicción clínica.",
            "Se usa el último análisis por texto y se excluyen inferencias refutadas. Se fecha por el texto original; los análisis antiguos sin fuente usan su timestamp y se cuentan como fallback.",
            "Si existe un enlace de procedencia, su texto debe pertenecer al mismo paciente; los enlaces rotos o cruzados se excluyen del cálculo.",
            "Las categorías psicosociales son frecuencias de observaciones aceptadas, no diagnósticos. Texto libre (explicaciones, citas, retrato e hilos abiertos) permanece en la evidencia y no se transforma en números.",
        ],
    }


def load_daily_statistics(db, patient_id, window_days: int = 30, *, now: datetime | None = None, checkins=None, linguistic_signals=None, observations=None) -> dict:
    """Load only this patient's metadata; callers enforce existing RBAC."""
    from app.models import Agent2AnalysisTrace, AlfaSignal, ChatMessage, CheckIn, DiaryEntry, PsychosocialObservation

    start, end = window_bounds(window_days, now)
    if checkins is None:
        checkins = db.query(CheckIn).filter(CheckIn.user_id == patient_id, CheckIn.created_at >= start, CheckIn.created_at <= end).all()
    if linguistic_signals is None:
        linguistic_signals = db.query(AlfaSignal).filter(AlfaSignal.user_id == patient_id, AlfaSignal.signal_type == "linguistic_analysis", AlfaSignal.timestamp >= start, AlfaSignal.timestamp <= end).all()
    if observations is None:
        observations = db.query(PsychosocialObservation).filter(PsychosocialObservation.user_id == patient_id, PsychosocialObservation.observed_at >= start, PsychosocialObservation.observed_at <= end).all()
    trace_ids = [row.agent2_trace_id for row in linguistic_signals if row.agent2_trace_id]
    traces = db.query(Agent2AnalysisTrace).filter(Agent2AnalysisTrace.user_id == patient_id, Agent2AnalysisTrace.id.in_(trace_ids)).all() if trace_ids else []
    source_keys = {_source_key(row) for row in [*traces, *observations]} - {None}
    source_times = {}
    for source_type, model in (("chat_message", ChatMessage), ("diary_entry", DiaryEntry)):
        ids = [uuid.UUID(source_id) for kind, source_id in source_keys if kind == source_type]
        if ids:
            # Never fetch source text for an aggregate calculation.
            query = db.query(model.id, model.created_at).filter(model.user_id == patient_id, model.id.in_(ids))
            if model is ChatMessage:
                query = query.filter(ChatMessage.role == "user")
            for row in query.all():
                source_times[(source_type, str(row.id))] = row.created_at
    return aggregate_daily_statistics(checkins, linguistic_signals, observations, traces_by_id={row.id: row for row in traces}, source_times=source_times, strict_sources=True, window_days=window_days, now=end)
