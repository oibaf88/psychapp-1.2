"""Tests for the timeline service module (backend/app/services/timeline.py).

Verifies timeline building logic, checkin & structural score signal merging,
baseline availability checking, date filtering windows, and user isolation.
"""
import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models import AlfaSignal, Baseline, CheckIn
from app.services import timeline


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)
        self._filters = []

    def filter(self, *args, **kwargs):
        # Store filters if needed or return self
        return self

    def order_by(self, *args):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class MockSQLAlchemySession:
    """Mock DB session that simulates real SQLAlchemy query filtering for CheckIn, AlfaSignal, Baseline."""

    def __init__(self, checkins=None, scores=None, baselines=None):
        self.checkins = checkins or []
        self.scores = scores or []
        self.baselines = baselines or []
        self.queries = []

    def query(self, model):
        self.queries.append(model)
        if model is CheckIn:
            return MockQuery(self.checkins, model)
        if model is AlfaSignal:
            return MockQuery(self.scores, model)
        if model is Baseline:
            return MockQuery(self.baselines, model)
        return MockQuery([], model)


class MockQuery:
    def __init__(self, items, model):
        self.items = items
        self.model = model

    def filter(self, *criterion):
        # We can implement minimal evaluation of binary expressions if needed,
        # or filter items based on criteria.
        filtered = list(self.items)
        for c in criterion:
            # SQLAlchemy BinaryExpression evaluation simulation
            left_col = getattr(c.left, "name", None) if hasattr(c, "left") else None
            right_val = c.right.value if hasattr(c, "right") and hasattr(c.right, "value") else None
            operator = getattr(c.operator, "__name__", str(c.operator)) if hasattr(c, "operator") else ""

            if left_col and right_val is not None:
                if "eq" in operator or operator == "eq":
                    filtered = [item for item in filtered if getattr(item, left_col, None) == right_val]
                elif "ge" in operator or operator == "ge":
                    filtered = [item for item in filtered if getattr(item, left_col, None) >= right_val]

        return MockQuery(filtered, self.model)

    def order_by(self, *criterion):
        return self

    def all(self):
        return self.items

    def first(self):
        for item in self.items:
            if getattr(item, "is_active", True):
                return item
        return self.items[0] if self.items else None


class TimelineServiceTests(unittest.TestCase):
    def setUp(self):
        self.user_id = uuid.uuid4()
        self.other_user_id = uuid.uuid4()

    def test_build_timeline_empty_returns_default_structure(self):
        db = MockSQLAlchemySession()
        res = timeline.build_timeline(db, self.user_id, window_days=30)
        self.assertEqual(res["points"], [])
        self.assertFalse(res["baseline_available"])
        self.assertEqual(res["window_days"], 30)

    def test_build_timeline_merges_checkin_and_structural_score_on_same_day(self):
        d1 = datetime(2026, 8, 10, 14, 30, 0)
        checkin = SimpleNamespace(
            user_id=self.user_id,
            created_at=d1,
            mood=4.0,
            craving=2.0,
            sleep_hours=7.5,
            self_efficacy=3.0,
        )
        score = SimpleNamespace(
            user_id=self.user_id,
            signal_type="structural_score",
            timestamp=d1,
            value={"score": 0.85},
            confidence_band="stable",
        )
        db = MockSQLAlchemySession(checkins=[checkin], scores=[score])

        res = timeline.build_timeline(db, self.user_id)
        self.assertEqual(len(res["points"]), 1)
        pt = res["points"][0]
        self.assertEqual(pt["date"], "2026-08-10")
        self.assertEqual(pt["mood"], 4.0)
        self.assertEqual(pt["craving"], 2.0)
        self.assertEqual(pt["sleep_hours"], 7.5)
        self.assertEqual(pt["self_efficacy"], 3.0)
        self.assertEqual(pt["structural_score"], 0.85)
        self.assertEqual(pt["confidence_band"], "stable")

    def test_build_timeline_handles_multiple_days_sorted(self):
        d1 = datetime(2026, 8, 12, 10, 0, 0)
        d2 = datetime(2026, 8, 10, 10, 0, 0)
        c1 = SimpleNamespace(user_id=self.user_id, created_at=d1, mood=5.0, craving=1.0, sleep_hours=8.0, self_efficacy=4.0)
        c2 = SimpleNamespace(user_id=self.user_id, created_at=d2, mood=2.0, craving=4.0, sleep_hours=5.0, self_efficacy=2.0)
        db = MockSQLAlchemySession(checkins=[c1, c2])

        res = timeline.build_timeline(db, self.user_id)
        self.assertEqual(len(res["points"]), 2)
        self.assertEqual(res["points"][0]["date"], "2026-08-10")
        self.assertEqual(res["points"][1]["date"], "2026-08-12")

    def test_build_timeline_user_isolation_and_date_window_filtering(self):
        now = datetime.utcnow()
        recent_date = now - timedelta(days=5)
        old_date = now - timedelta(days=40)

        # User checkin within window
        c_user = SimpleNamespace(user_id=self.user_id, created_at=recent_date, mood=3.0, craving=2.0, sleep_hours=7.0, self_efficacy=3.0)
        # User checkin outside window
        c_old = SimpleNamespace(user_id=self.user_id, created_at=old_date, mood=1.0, craving=5.0, sleep_hours=4.0, self_efficacy=1.0)
        # Other user checkin within window
        c_other = SimpleNamespace(user_id=self.other_user_id, created_at=recent_date, mood=5.0, craving=0.0, sleep_hours=9.0, self_efficacy=5.0)

        db = MockSQLAlchemySession(checkins=[c_user, c_old, c_other])
        res = timeline.build_timeline(db, self.user_id, window_days=30)
        self.assertEqual(len(res["points"]), 1)
        self.assertEqual(res["points"][0]["date"], recent_date.strftime("%Y-%m-%d"))

    def test_build_timeline_reflects_active_baseline_status(self):
        active_baseline = SimpleNamespace(user_id=self.user_id, is_active=True)
        db = MockSQLAlchemySession(baselines=[active_baseline])
        res = timeline.build_timeline(db, self.user_id)
        self.assertTrue(res["baseline_available"])

    def test_build_timeline_custom_window_days(self):
        db = MockSQLAlchemySession()
        res = timeline.build_timeline(db, self.user_id, window_days=14)
        self.assertEqual(res["window_days"], 14)

    def test_build_timeline_score_with_missing_value_dict(self):
        d1 = datetime(2026, 8, 11, 12, 0, 0)
        score = SimpleNamespace(
            user_id=self.user_id,
            signal_type="structural_score",
            timestamp=d1,
            value=None,
            confidence_band="transition",
        )
        db = MockSQLAlchemySession(scores=[score])
        res = timeline.build_timeline(db, self.user_id)
        self.assertEqual(len(res["points"]), 1)
        pt = res["points"][0]
        self.assertIsNone(pt["structural_score"])
        self.assertEqual(pt["confidence_band"], "transition")


if __name__ == "__main__":
    unittest.main()
