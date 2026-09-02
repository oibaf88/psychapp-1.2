from datetime import datetime, timezone, timedelta
from app.schemas import _utc_iso


def test_utc_iso_none():
    assert _utc_iso(None) is None


def test_utc_iso_naive_datetime():
    dt = datetime(2025, 3, 1, 14, 30, 0)
    result = _utc_iso(dt)
    assert result == "2025-03-01T14:30:00+00:00"


def test_utc_iso_aware_utc_datetime():
    dt = datetime(2025, 3, 1, 14, 30, 0, tzinfo=timezone.utc)
    result = _utc_iso(dt)
    assert result == "2025-03-01T14:30:00+00:00"


def test_utc_iso_aware_non_utc_timezone():
    # EST is UTC-5
    est = timezone(timedelta(hours=-5))
    dt = datetime(2025, 3, 1, 14, 30, 0, tzinfo=est)
    result = _utc_iso(dt)
    # 14:30 EST corresponds to 19:30 UTC
    assert result == "2025-03-01T19:30:00+00:00"


def test_utc_iso_datetime_with_microseconds():
    dt = datetime(2025, 3, 1, 14, 30, 0, 123456)
    result = _utc_iso(dt)
    assert result == "2025-03-01T14:30:00.123456+00:00"
