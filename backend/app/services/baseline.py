"""
Local, transparent equivalent of the "Alfa ML" structural_score /
confidence_band engine referenced throughout the spec docs.

The docs (doc 1, section on the "Motor analítico") explicitly sanction
simple, explainable statistics -- rolling Z-score / IQR -- for the
prototype phase, and only mark EWMA/CUSUM/Bayesian changepoint methods as
later research work. This module implements exactly that prototype-phase
method, entirely locally (no network calls, no third-party service).

Deliberate deviation from the docs: doc 2 sketches piping this
calculation through a third-party service ("AlphaInfo.io" / a pip
package called `alphainfo`) using an API key. That integration was
NOT implemented -- see README "Assumptions and gaps" for why (unverified
third party, would send sensitive mental-health signal data off-device,
contradicts the docs' own privacy-by-design principles). This module
reproduces the same statistical idea (z-score-based structural
similarity to a personal baseline) fully locally instead.
"""
import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AlfaSignal, Baseline, CheckIn

BASELINE_WINDOW_DAYS = 21
RECENT_WINDOW_DAYS = 7
MIN_CHECKINS_FOR_BASELINE = 5

# How long an active baseline describes the present.
#
# It used to be forever: `get_active_baseline(...) or compute_or_refresh_...`
# created one on first use and never looked again, so a person's "normal" was
# fixed by their first three weeks in treatment and stayed there. Someone who
# genuinely improved kept being measured against how they were at their worst,
# and someone who deteriorated slowly drifted out of their own baseline
# without any single reading looking unusual.
#
# Recomputed on a 21-day window, so the baseline follows the person at the
# same pace it was built from. Long enough that a bad fortnight does not
# redefine normal; short enough that a season of change eventually does.
BASELINE_MAX_AGE_DAYS = 21

# craving is "inverted" (lower is better) so we flip sign before z-scoring
VARIABLES = ("mood", "craving_inv", "sleep_hours", "self_efficacy")

# Engineering safeguards, NOT clinical cut-offs or a validated instrument.
# An almost constant series must not turn a one-point change into dozens of
# standard deviations; a completely constant series must still detect change.
STD_FLOORS = {"mood": 1.0, "craving_inv": 1.0, "sleep_hours": 0.5, "self_efficacy": 1.0}
CALCULATION_VERSION = "structural-v2"
# Preserve the old descriptive band's deviation boundaries, rather than
# reusing its score thresholds after changing the similarity transform.
STABLE_MAX_COMPOSITE_Z = 1.2
TRANSITION_MAX_COMPOSITE_Z = 1.95


@dataclass
class StructuralScoreResult:
    score: float | None
    confidence_band: str  # stable | transition | unstable | insufficient_data
    z_scores: dict[str, float]
    baseline_n: int
    recent_n: int
    baseline_stats: dict[str, dict[str, float]]
    recent_means: dict[str, float]
    composite_z: float | None
    # Lower score means greater adverse deviation; it is not a probability
    # of suicide, relapse, or illness. Sleep is bilateral (change, not benefit).
    deterioration_score: float | None = None
    deterioration_band: str = "insufficient_data"
    adverse_composite_z: float | None = None
    favourable_composite_z: float | None = None
    adverse_z_scores: dict[str, float] = field(default_factory=dict)
    effective_stds: dict[str, float] = field(default_factory=dict)
    recent_counts: dict[str, int] = field(default_factory=dict)
    baseline_is_stale: bool = False
    calculation_version: str = CALCULATION_VERSION


@dataclass
class TrendResult:
    label: str
    slope: float | None
    sample_count: int
    increasing_threshold: float = 0.15
    decreasing_threshold: float = -0.15


def _finite_number(value) -> float | None:
    """Unknown, malformed, and non-finite values are not observations of zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _checkin_vector(c: CheckIn) -> dict[str, float]:
    vector = {}
    for key in VARIABLES:
        raw = _finite_number(getattr(c, "craving" if key == "craving_inv" else key, None))
        upper = 24.0 if key == "sleep_hours" else 10.0
        if raw is not None and 0.0 <= raw <= upper:
            vector[key] = 10.0 - raw if key == "craving_inv" else raw
    return vector


def _baseline_sample_count(stats: dict) -> int:
    counts = [_finite_number(_axis_stats(stats, key).get("n")) for key in VARIABLES]
    return int(min(counts)) if all(n is not None and n >= 0 for n in counts) else 0


def _axis_stats(stats: dict, key: str) -> dict:
    value = stats.get(key)
    return value if isinstance(value, dict) else {}


def _baseline_is_stale(baseline: Baseline, now: datetime | None = None) -> bool:
    # window_end dates the underlying observations, even if an old baseline
    # was copied/imported into a newly created database row.
    reference = getattr(baseline, "window_end", None) or getattr(baseline, "created_at", None)
    if not isinstance(reference, datetime):
        return True
    if reference.tzinfo is not None:
        reference = reference.astimezone(timezone.utc).replace(tzinfo=None)
    current = now or datetime.utcnow()
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current - reference >= timedelta(days=BASELINE_MAX_AGE_DAYS)


def _deviation_band(composite_z: float) -> str:
    if composite_z <= STABLE_MAX_COMPOSITE_Z:
        return "stable"
    if composite_z <= TRANSITION_MAX_COMPOSITE_Z:
        return "transition"
    return "unstable"


def _similarity(composite_z: float) -> float:
    # Smooth and strictly positive for every finite deviation. Previously
    # max(0, 1 - z/3) collapsed all z >= 3 to zero, including improvements.
    return 1.0 / (1.0 + composite_z)


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = statistics.fmean(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return mean, std


def compute_or_refresh_baseline(db: Session, user_id) -> Baseline | None:
    now = datetime.utcnow()
    window_start = now - timedelta(days=BASELINE_WINDOW_DAYS)
    checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id, CheckIn.created_at >= window_start, CheckIn.created_at <= now)
        .all()
    )
    if len(checkins) < MIN_CHECKINS_FOR_BASELINE:
        return None

    vectors = [_checkin_vector(c) for c in checkins]
    stats: dict[str, dict[str, float]] = {}
    for var in VARIABLES:
        values = [v[var] for v in vectors if var in v]
        if len(values) < MIN_CHECKINS_FOR_BASELINE:
            return None
        mean, std = _mean_std(values)
        stats[var] = {"mean": mean, "std": std, "n": len(values)}

    db.query(Baseline).filter(Baseline.user_id == user_id, Baseline.is_active == True).update(  # noqa: E712
        {"is_active": False}
    )
    baseline = Baseline(
        user_id=user_id,
        window_start=window_start,
        window_end=now,
        stats=stats,
        is_active=True,
    )
    db.add(baseline)
    db.commit()
    db.refresh(baseline)
    return baseline


def get_active_baseline(db: Session, user_id) -> Baseline | None:
    return (
        db.query(Baseline)
        .filter(Baseline.user_id == user_id, Baseline.is_active == True)  # noqa: E712
        .order_by(Baseline.created_at.desc())
        .first()
    )


def _current_baseline(db: Session, user_id) -> Baseline | None:
    """The active baseline, recomputed when it has aged out.

    A stale baseline is not discarded before its replacement exists:
    `compute_or_refresh_baseline` returns None when there are too few recent
    check-ins, and in that case the old one keeps serving. Losing a person's
    baseline because they stopped checking in for a fortnight would take the
    structural axis offline exactly when it is worth watching.
    """
    active = get_active_baseline(db, user_id)
    if active is None:
        return compute_or_refresh_baseline(db, user_id)

    if not _baseline_is_stale(active):
        return active
    return compute_or_refresh_baseline(db, user_id) or active


def compute_structural_score(db: Session, user_id) -> StructuralScoreResult:
    baseline = _current_baseline(db, user_id)
    now = datetime.utcnow()
    if baseline is None:
        return StructuralScoreResult(
            score=None,
            confidence_band="insufficient_data",
            z_scores={},
            baseline_n=0,
            recent_n=0,
            baseline_stats={},
            recent_means={},
            composite_z=None,
        )

    baseline_stats = baseline.stats if isinstance(baseline.stats, dict) else {}
    baseline_is_stale = _baseline_is_stale(baseline, now)
    recent_start = now - timedelta(days=RECENT_WINDOW_DAYS)
    recent = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id, CheckIn.created_at >= recent_start, CheckIn.created_at <= now)
        .all()
    )
    if not recent:
        return StructuralScoreResult(
            score=None,
            confidence_band="insufficient_data",
            z_scores={},
            baseline_n=_baseline_sample_count(baseline_stats),
            recent_n=0,
            baseline_stats=baseline_stats,
            recent_means={},
            composite_z=None,
            baseline_is_stale=baseline_is_stale,
        )

    recent_vectors = [_checkin_vector(c) for c in recent]
    z_scores: dict[str, float] = {}
    recent_means: dict[str, float] = {}
    recent_counts: dict[str, int] = {}
    effective_stds: dict[str, float] = {}
    adverse_z_scores: dict[str, float] = {}
    abs_z_values: list[float] = []
    adverse_values: list[float] = []
    favourable_values: list[float] = []
    for var in VARIABLES:
        var_stats = _axis_stats(baseline_stats, var)
        mean = _finite_number(var_stats.get("mean"))
        std = _finite_number(var_stats.get("std"))
        n = _finite_number(var_stats.get("n"))
        values = [v[var] for v in recent_vectors if var in v]
        recent_counts[var] = len(values)
        if not values:
            continue
        recent_mean = statistics.fmean(values)
        recent_means[var] = round(recent_mean, 3)
        upper = 24.0 if var == "sleep_hours" else 10.0
        if mean is None or not 0 <= mean <= upper or std is None or std < 0 or n is None or n < MIN_CHECKINS_FOR_BASELINE:
            continue
        effective_std = max(std, STD_FLOORS[var])
        effective_stds[var] = effective_std
        z = (recent_mean - mean) / effective_std
        z_scores[var] = round(z, 3)
        abs_z_values.append(abs(z))
        # Sleep duration has no universally favourable direction. Both less
        # and more than the personal baseline merit review, without claiming
        # either change establishes a clinical deterioration on its own.
        adverse = abs(z) if var == "sleep_hours" else max(-z, 0.0)
        favourable = 0.0 if var == "sleep_hours" else max(z, 0.0)
        adverse_z_scores[var] = round(adverse, 3)
        adverse_values.append(adverse)
        favourable_values.append(favourable)

    # The four-axis composite is not comparable if a missing axis is silently
    # replaced by zero, or if the denominator changes from four to three.
    if len(z_scores) != len(VARIABLES):
        return StructuralScoreResult(
            score=None, confidence_band="insufficient_data", z_scores=z_scores,
            baseline_n=_baseline_sample_count(baseline_stats), recent_n=len(recent),
            baseline_stats=baseline_stats, recent_means=recent_means, composite_z=None,
            adverse_z_scores=adverse_z_scores, effective_stds=effective_stds,
            recent_counts=recent_counts, baseline_is_stale=baseline_is_stale,
        )

    composite_z = statistics.fmean(abs_z_values)
    adverse_z = statistics.fmean(adverse_values)
    favourable_z = statistics.fmean(favourable_values)
    score = _similarity(composite_z)
    deterioration_score = _similarity(adverse_z)
    band = _deviation_band(composite_z)
    deterioration_band = _deviation_band(adverse_z)

    signal = AlfaSignal(
        user_id=user_id,
        signal_type="structural_score",
        value={
            "score": score, "z_scores": z_scores, "composite_z": round(composite_z, 3),
            "deterioration_score": deterioration_score, "deterioration_band": deterioration_band,
            "adverse_composite_z": round(adverse_z, 3), "favourable_composite_z": round(favourable_z, 3),
            "adverse_z_scores": adverse_z_scores, "effective_stds": effective_stds,
            "recent_counts": recent_counts, "baseline_is_stale": baseline_is_stale,
            "calculation_version": CALCULATION_VERSION,
            "baseline_id": str(baseline.id) if getattr(baseline, "id", None) is not None else None,
        },
        confidence_band=band,
    )
    db.add(signal)
    db.commit()

    return StructuralScoreResult(
        score=score,
        confidence_band=band,
        z_scores=z_scores,
        baseline_n=_baseline_sample_count(baseline_stats),
        recent_n=len(recent),
        baseline_stats=baseline_stats,
        recent_means=recent_means,
        composite_z=round(composite_z, 3),
        deterioration_score=deterioration_score,
        deterioration_band=deterioration_band,
        adverse_composite_z=round(adverse_z, 3),
        favourable_composite_z=round(favourable_z, 3),
        adverse_z_scores=adverse_z_scores,
        effective_stds=effective_stds,
        recent_counts=recent_counts,
        baseline_is_stale=baseline_is_stale,
    )


def calculate_trend(db: Session, user_id, values: list[float]) -> str:
    """
    Very small linear-regression-slope trend classifier, matching the
    `calcular_tendencia` helper in doc 18 (simple regression, insufficient
    data below 3 points, thresholded slope -> aumentando/empeorando/estable).
    """
    return calculate_trend_detail(values).label


def calculate_trend_detail(values: list[float]) -> TrendResult:
    """Return the label *and* the exact regression inputs used to derive it."""
    if len(values) < 3:
        return TrendResult(label="insuficiente", slope=None, sample_count=len(values))

    n = len(values)
    xs = list(range(n))
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(values)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values))
    denominator = sum((x - x_mean) ** 2 for x in xs) or 1.0
    slope = numerator / denominator

    # thresholds are intentionally conservative / symmetric
    if slope > 0.15:
        return TrendResult(label="aumentando", slope=round(slope, 4), sample_count=n)
    if slope < -0.15:
        return TrendResult(label="empeorando", slope=round(slope, 4), sample_count=n)
    return TrendResult(label="estable", slope=round(slope, 4), sample_count=n)
