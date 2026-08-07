from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import AlfaSignal, CheckIn
from app.services.baseline import get_active_baseline

DEFAULT_WINDOW_DAYS = 30


def build_timeline(db: Session, user_id, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    since = datetime.utcnow() - timedelta(days=window_days)

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

    by_day: dict[str, dict] = defaultdict(dict)
    for c in checkins:
        day = c.created_at.strftime("%Y-%m-%d")
        by_day[day].update(
            {"mood": c.mood, "craving": c.craving, "sleep_hours": c.sleep_hours, "self_efficacy": c.self_efficacy}
        )
    for s in scores:
        day = s.timestamp.strftime("%Y-%m-%d")
        by_day[day]["structural_score"] = (s.value or {}).get("score")
        by_day[day]["confidence_band"] = s.confidence_band

    points = [{"date": day, **values} for day, values in sorted(by_day.items())]
    return {
        "points": points,
        "baseline_available": get_active_baseline(db, user_id) is not None,
        "window_days": window_days,
    }
