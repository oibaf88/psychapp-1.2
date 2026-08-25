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
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

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


@dataclass
class TrendResult:
    label: str
    slope: float | None
    sample_count: int
    increasing_threshold: float = 0.15
    decreasing_threshold: float = -0.15


def _checkin_vector(c: CheckIn) -> dict[str, float]:
    return {
        "mood": float(c.mood),
        "craving_inv": 10.0 - float(c.craving),
        "sleep_hours": float(c.sleep_hours),
        "self_efficacy": float(c.self_efficacy),
    }


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = statistics.fmean(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    return mean, std


def compute_or_refresh_baseline(db: Session, user_id) -> Baseline | None:
    window_start = datetime.utcnow() - timedelta(days=BASELINE_WINDOW_DAYS)
    checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id, CheckIn.created_at >= window_start)
        .all()
    )
    if len(checkins) < MIN_CHECKINS_FOR_BASELINE:
        return None

    vectors = [_checkin_vector(c) for c in checkins]
    stats: dict[str, dict[str, float]] = {}
    for var in VARIABLES:
        values = [v[var] for v in vectors]
        mean, std = _mean_std(values)
        stats[var] = {"mean": mean, "std": std, "n": len(values)}

    db.query(Baseline).filter(Baseline.user_id == user_id, Baseline.is_active == True).update(  # noqa: E712
        {"is_active": False}
    )
    baseline = Baseline(
        user_id=user_id,
        window_start=window_start,
        window_end=datetime.utcnow(),
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

    created = active.created_at or datetime.utcnow()
    if datetime.utcnow() - created < timedelta(days=BASELINE_MAX_AGE_DAYS):
        return active
    return compute_or_refresh_baseline(db, user_id) or active


def compute_structural_score(db: Session, user_id) -> StructuralScoreResult:
    baseline = _current_baseline(db, user_id)
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

    recent_start = datetime.utcnow() - timedelta(days=RECENT_WINDOW_DAYS)
    recent = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id, CheckIn.created_at >= recent_start)
        .all()
    )
    if not recent:
        return StructuralScoreResult(
            score=None,
            confidence_band="insufficient_data",
            z_scores={},
            baseline_n=sum(v.get("n", 0) for v in baseline.stats.values()) // max(len(VARIABLES), 1),
            recent_n=0,
            baseline_stats=baseline.stats,
            recent_means={},
            composite_z=None,
        )

    recent_vectors = [_checkin_vector(c) for c in recent]
    z_scores: dict[str, float] = {}
    recent_means: dict[str, float] = {}
    abs_z_values: list[float] = []
    for var in VARIABLES:
        var_stats = baseline.stats.get(var, {})
        mean = var_stats.get("mean", 0.0)
        std = var_stats.get("std", 0.0)
        recent_mean = statistics.fmean([v[var] for v in recent_vectors])
        recent_means[var] = round(recent_mean, 3)
        if std > 0:
            z = (recent_mean - mean) / std
        else:
            z = 0.0
        z_scores[var] = round(z, 3)
        abs_z_values.append(abs(z))

    composite_z = statistics.fmean(abs_z_values) if abs_z_values else 0.0
    # score 1.0 = matches baseline exactly; approaches 0 as composite_z -> 3
    score = max(0.0, 1.0 - composite_z / 3.0)
    score = round(min(score, 1.0), 3)

    if score >= 0.6:
        band = "stable"
    elif score >= 0.35:
        band = "transition"
    else:
        band = "unstable"

    signal = AlfaSignal(
        user_id=user_id,
        signal_type="structural_score",
        value={"score": score, "z_scores": z_scores, "composite_z": round(composite_z, 3)},
        confidence_band=band,
    )
    db.add(signal)
    db.commit()

    return StructuralScoreResult(
        score=score,
        confidence_band=band,
        z_scores=z_scores,
        baseline_n=sum(v.get("n", 0) for v in baseline.stats.values()) // max(len(VARIABLES), 1),
        recent_n=len(recent),
        baseline_stats=baseline.stats,
        recent_means=recent_means,
        composite_z=round(composite_z, 3),
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
