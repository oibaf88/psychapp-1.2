from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AlfaSignal, CheckIn
from app.services.baseline import get_active_baseline
from app.services import daily_statistics

DEFAULT_WINDOW_DAYS = 30


def build_timeline(db: Session, user_id, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    window_days = max(1, min(int(window_days), 365))
    now = datetime.utcnow()
    since, _ = daily_statistics.window_bounds(window_days, now)

    checkins = (
        db.query(CheckIn)
        .filter(CheckIn.user_id == user_id, CheckIn.created_at >= since)
        .order_by(CheckIn.created_at.asc())
        .all()
    )
    scores = (
        db.query(AlfaSignal)
        .filter(
            AlfaSignal.user_id == user_id,
            AlfaSignal.signal_type == "structural_score",
            AlfaSignal.timestamp >= since,
        )
        .order_by(AlfaSignal.timestamp.asc())
        .all()
    )

    statistics = daily_statistics.load_daily_statistics(
        db, user_id, window_days, now=now, checkins=checkins,
    )
    by_day: dict[str, dict] = defaultdict(dict)
    for point in statistics["daily"]:
        by_day[point["date"]].update({key: point[key] for key in daily_statistics.CHECKIN_KEYS})
    for s in scores:
        if daily_statistics.utc_datetime(s.timestamp).replace(tzinfo=None) > now:
            continue
        day = daily_statistics.local_day(s.timestamp)
        by_day[day]["structural_score"] = (s.value or {}).get("score")
        by_day[day]["confidence_band"] = s.confidence_band
        by_day[day]["structural_calculation_version"] = (s.value or {}).get("calculation_version") or "structural-v1"

    points = [{"date": day, **values} for day, values in sorted(by_day.items())]
    return {
        "points": points,
        "baseline_available": get_active_baseline(db, user_id) is not None,
        "window_days": window_days,
        "daily_statistics": statistics,
    }
