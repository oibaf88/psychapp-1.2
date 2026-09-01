"""Unit and integration tests for timeline service functions in backend/app/services/timeline.py."""
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Agent2AnalysisTrace, AlfaSignal, Baseline, CheckIn, User
from app.services import daily_statistics, timeline

NOW = datetime(2026, 8, 31, 18, 0, 0)


class _Clock(datetime):
    @classmethod
    def utcnow(cls):
        return NOW


class TimelineServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        for model in (User, CheckIn, Baseline, AlfaSignal, Agent2AnalysisTrace):
            model.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db: Session = self.sessions()

        self.user1 = self._create_user("patient1@example.test")
        self.user2 = self._create_user("patient2@example.test")

        self.clock_patch = patch.object(timeline, "datetime", _Clock)
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.db.close()
        self.engine.dispose()

    def _create_user(self, email):
        user = User(
            id=uuid.uuid4(),
            email=email,
            display_name="Test User",
            hashed_password="hashed_pw",
            role="patient",
        )
        self.db.add(user)
        self.db.commit()
        return user

    def _create_checkin(self, user_id, created_at, mood=5, craving=3, sleep_hours=7.5, self_efficacy=6, checkin_id=None):
        c = CheckIn(
            id=checkin_id or uuid.uuid4(),
            user_id=user_id,
            mood=mood,
            craving=craving,
            sleep_hours=sleep_hours,
            self_efficacy=self_efficacy,
            created_at=created_at,
        )
        self.db.add(c)
        self.db.commit()
        return c

    def _create_alfa_signal(self, user_id, timestamp, score=0.85, confidence_band="stable", calc_version=None):
        val = {"score": score}
        if calc_version is not None:
            val["calculation_version"] = calc_version
        s = AlfaSignal(
            id=uuid.uuid4(),
            user_id=user_id,
            signal_type="structural_score",
            value=val,
            confidence_band=confidence_band,
            timestamp=timestamp,
            is_active=True,
        )
        self.db.add(s)
        self.db.commit()
        return s

    def _create_baseline(self, user_id, is_active=True):
        b = Baseline(
            id=uuid.uuid4(),
            user_id=user_id,
            window_start=NOW - timedelta(days=21),
            window_end=NOW,
            stats={"mood": {"mean": 5.0, "std": 1.0, "n": 10}},
            is_active=is_active,
        )
        self.db.add(b)
        self.db.commit()
        return b

    # --- Tests for build_patient_timeline ---

    def test_build_patient_timeline_empty(self):
        res = timeline.build_patient_timeline(self.db, self.user1.id, window_days=14)
        self.assertEqual(res["points"], [])
        self.assertEqual(res["window_days"], 14)

    def test_build_patient_timeline_basic(self):
        day1 = NOW - timedelta(days=2)
        day2 = NOW - timedelta(days=1)
        self._create_checkin(self.user1.id, created_at=day1, mood=4, craving=2, sleep_hours=8.0, self_efficacy=7)
        self._create_checkin(self.user1.id, created_at=day2, mood=6, craving=5, sleep_hours=6.5, self_efficacy=5)

        res = timeline.build_patient_timeline(self.db, self.user1.id, window_days=7)
        self.assertEqual(res["window_days"], 7)
        self.assertEqual(len(res["points"]), 2)
        self.assertEqual(res["points"][0]["mood"], 4)
        self.assertEqual(res["points"][0]["craving"], 2)
        self.assertEqual(res["points"][1]["mood"], 6)

    def test_build_patient_timeline_last_checkin_per_day_wins(self):
        morning = datetime(2026, 8, 30, 9, 0, 0)
        afternoon = datetime(2026, 8, 30, 16, 0, 0)

        self._create_checkin(self.user1.id, created_at=morning, mood=2, craving=8, sleep_hours=4.0, self_efficacy=3)
        self._create_checkin(self.user1.id, created_at=afternoon, mood=7, craving=3, sleep_hours=7.5, self_efficacy=8)

        res = timeline.build_patient_timeline(self.db, self.user1.id, window_days=7)
        self.assertEqual(len(res["points"]), 1)
        point = res["points"][0]
        self.assertEqual(point["date"], "2026-08-30")
        self.assertEqual(point["mood"], 7)
        self.assertEqual(point["craving"], 3)
        self.assertEqual(point["sleep_hours"], 7.5)
        self.assertEqual(point["self_efficacy"], 8)

    def test_build_patient_timeline_same_timestamp_id_tiebreaking(self):
        same_time = datetime(2026, 8, 30, 12, 0, 0)
        id1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
        id2 = uuid.UUID("22222222-2222-2222-2222-222222222222")

        self._create_checkin(self.user1.id, created_at=same_time, mood=3, checkin_id=id1)
        self._create_checkin(self.user1.id, created_at=same_time, mood=8, checkin_id=id2)

        res = timeline.build_patient_timeline(self.db, self.user1.id, window_days=7)
        self.assertEqual(len(res["points"]), 1)
        self.assertEqual(res["points"][0]["mood"], 8)

    def test_build_patient_timeline_window_days_clamping(self):
        res_min = timeline.build_patient_timeline(self.db, self.user1.id, window_days=-10)
        self.assertEqual(res_min["window_days"], 1)

        res_zero = timeline.build_patient_timeline(self.db, self.user1.id, window_days=0)
        self.assertEqual(res_zero["window_days"], 1)

        res_max = timeline.build_patient_timeline(self.db, self.user1.id, window_days=1000)
        self.assertEqual(res_max["window_days"], 365)

        res_default = timeline.build_patient_timeline(self.db, self.user1.id)
        self.assertEqual(res_default["window_days"], timeline.DEFAULT_WINDOW_DAYS)

    def test_build_patient_timeline_user_isolation(self):
        t = NOW - timedelta(days=1)
        self._create_checkin(self.user1.id, created_at=t, mood=5)
        self._create_checkin(self.user2.id, created_at=t, mood=10)

        res1 = timeline.build_patient_timeline(self.db, self.user1.id, window_days=7)
        self.assertEqual(len(res1["points"]), 1)
        self.assertEqual(res1["points"][0]["mood"], 5)

        res2 = timeline.build_patient_timeline(self.db, self.user2.id, window_days=7)
        self.assertEqual(len(res2["points"]), 1)
        self.assertEqual(res2["points"][0]["mood"], 10)

    def test_build_patient_timeline_date_range_filtering(self):
        too_old = NOW - timedelta(days=10)
        in_range = NOW - timedelta(days=2)
        in_future = NOW + timedelta(days=2)

        self._create_checkin(self.user1.id, created_at=too_old, mood=1)
        self._create_checkin(self.user1.id, created_at=in_range, mood=5)
        self._create_checkin(self.user1.id, created_at=in_future, mood=9)

        res = timeline.build_patient_timeline(self.db, self.user1.id, window_days=5)
        self.assertEqual(len(res["points"]), 1)
        self.assertEqual(res["points"][0]["mood"], 5)

    # --- Tests for build_timeline ---

    def test_build_timeline_empty(self):
        res = timeline.build_timeline(self.db, self.user1.id, window_days=14)
        self.assertEqual(res["points"], [])
        self.assertFalse(res["baseline_available"])
        self.assertEqual(res["window_days"], 14)
        self.assertIn("daily", res["daily_statistics"])

    def test_build_timeline_basic(self):
        t = NOW - timedelta(days=2)
        self._create_checkin(self.user1.id, created_at=t, mood=8, craving=2, sleep_hours=8.0, self_efficacy=9)
        self._create_alfa_signal(self.user1.id, timestamp=t, score=0.92, confidence_band="stable", calc_version="structural-v2")
        self._create_baseline(self.user1.id, is_active=True)

        res = timeline.build_timeline(self.db, self.user1.id, window_days=7)
        self.assertTrue(res["baseline_available"])
        self.assertEqual(res["window_days"], 7)
        self.assertEqual(len(res["points"]), 1)

        point = res["points"][0]
        self.assertEqual(point["mood"], 8)
        self.assertEqual(point["structural_score"], 0.92)
        self.assertEqual(point["confidence_band"], "stable")
        self.assertEqual(point["structural_calculation_version"], "structural-v2")

    def test_build_timeline_window_days_clamping(self):
        res_min = timeline.build_timeline(self.db, self.user1.id, window_days=0)
        self.assertEqual(res_min["window_days"], 1)

        res_max = timeline.build_timeline(self.db, self.user1.id, window_days=500)
        self.assertEqual(res_max["window_days"], 365)

    def test_build_timeline_ignores_future_scores(self):
        past_t = NOW - timedelta(days=2)
        future_t = NOW + timedelta(hours=5)

        self._create_checkin(self.user1.id, created_at=past_t, mood=6)
        self._create_alfa_signal(self.user1.id, timestamp=future_t, score=0.1, confidence_band="unstable")

        res = timeline.build_timeline(self.db, self.user1.id, window_days=7)
        self.assertEqual(len(res["points"]), 1)
        self.assertNotIn("structural_score", res["points"][0])

    def test_build_timeline_structural_score_calculation_version_fallback(self):
        t = NOW - timedelta(days=1)
        self._create_checkin(self.user1.id, created_at=t, mood=5)

        s = AlfaSignal(
            id=uuid.uuid4(),
            user_id=self.user1.id,
            signal_type="structural_score",
            value={"score": 0.75},
            confidence_band="stable",
            timestamp=t,
            is_active=True,
        )
        self.db.add(s)
        self.db.commit()

        res = timeline.build_timeline(self.db, self.user1.id, window_days=7)
        self.assertEqual(len(res["points"]), 1)
        point = res["points"][0]
        self.assertEqual(point["structural_score"], 0.75)
        self.assertEqual(point["structural_calculation_version"], "structural-v1")

    def test_build_timeline_baseline_available_flag(self):
        res_no_baseline = timeline.build_timeline(self.db, self.user1.id, window_days=7)
        self.assertFalse(res_no_baseline["baseline_available"])

        self._create_baseline(self.user1.id, is_active=True)
        res_with_baseline = timeline.build_timeline(self.db, self.user1.id, window_days=7)
        self.assertTrue(res_with_baseline["baseline_available"])

    def test_build_timeline_user_isolation(self):
        t = NOW - timedelta(days=1)
        self._create_checkin(self.user1.id, created_at=t, mood=3)
        self._create_checkin(self.user2.id, created_at=t, mood=9)

        self._create_alfa_signal(self.user1.id, timestamp=t, score=0.4, confidence_band="unstable")
        self._create_alfa_signal(self.user2.id, timestamp=t, score=0.9, confidence_band="stable")

        res1 = timeline.build_timeline(self.db, self.user1.id, window_days=7)
        self.assertEqual(len(res1["points"]), 1)
        self.assertEqual(res1["points"][0]["mood"], 3)
        self.assertEqual(res1["points"][0]["structural_score"], 0.4)

        res2 = timeline.build_timeline(self.db, self.user2.id, window_days=7)
        self.assertEqual(len(res2["points"]), 1)
        self.assertEqual(res2["points"][0]["mood"], 9)
        self.assertEqual(res2["points"][0]["structural_score"], 0.9)


if __name__ == "__main__":
    unittest.main()
