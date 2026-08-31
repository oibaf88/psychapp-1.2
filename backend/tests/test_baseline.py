"""Regression tests for descriptive similarity versus adverse deviation.

These are engineering guarantees for a transparent heuristic, not a validation
of a suicide/relapse instrument or of any clinical score threshold.
"""
import math
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models import AlfaSignal, Baseline
from app.services import baseline


def _baseline(*, means=None, stds=None, days_old=1):
    means = means or {key: 5.0 for key in baseline.VARIABLES}
    stds = stds or {key: 1.0 for key in baseline.VARIABLES}
    return SimpleNamespace(
        id=uuid.uuid4(), created_at=datetime.utcnow() - timedelta(days=days_old),
        window_end=datetime.utcnow() - timedelta(days=days_old),
        stats={key: {"mean": means[key], "std": stds[key], "n": 10} for key in baseline.VARIABLES},
    )


def _checkin(*, mood=5.0, craving_inv=5.0, sleep_hours=5.0, self_efficacy=5.0):
    return SimpleNamespace(
        mood=mood, craving=None if craving_inv is None else 10.0 - craving_inv,
        sleep_hours=sleep_hours, self_efficacy=self_efficacy,
    )


def _compute(active, checkins):
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = checkins
    with patch.object(baseline, "_current_baseline", return_value=active):
        result = baseline.compute_structural_score(db, uuid.uuid4())
    return result, db


class StructuralScoreTests(unittest.TestCase):
    def test_reported_saturation_case_remains_positive_and_not_adversely_unstable(self):
        # Before the fix these z values averaged 6.827 and forced score=0.
        # Low historical SD had amplified improvements in craving/efficacy.
        active = _baseline(
            means={"mood": 2.0, "craving_inv": 3.0, "sleep_hours": 5.0, "self_efficacy": 2.0},
            stds={"mood": 1.0, "craving_inv": 0.1, "sleep_hours": 1.0, "self_efficacy": 0.1},
        )
        recent = _checkin(mood=2.178, craving_inv=4.6375, sleep_hours=7.134, self_efficacy=2.8619)
        result, db = _compute(active, [recent] * 7)
        self.assertAlmostEqual(result.score, 0.453957, places=6)
        self.assertEqual(result.confidence_band, "transition")
        self.assertEqual(result.deterioration_band, "stable")
        self.assertAlmostEqual(result.adverse_composite_z, 0.534, places=3)
        self.assertEqual(result.effective_stds["craving_inv"], 1.0)
        self.assertEqual(result.effective_stds["self_efficacy"], 1.0)
        saved = db.add.call_args.args[0]
        self.assertIsInstance(saved, AlfaSignal)
        self.assertEqual(saved.value["calculation_version"], "structural-v2")
        self.assertEqual(saved.value["deterioration_band"], "stable")
        self.assertEqual(saved.value["baseline_id"], str(active.id))

    def test_improvement_and_worsening_have_same_similarity_but_different_adverse_score(self):
        improved, _ = _compute(_baseline(), [_checkin(mood=9, craving_inv=9, self_efficacy=9)])
        worsened, _ = _compute(_baseline(), [_checkin(mood=1, craving_inv=1, self_efficacy=1)])
        self.assertEqual(improved.score, worsened.score)
        self.assertEqual(improved.score, 0.25)
        self.assertEqual(improved.confidence_band, "unstable")
        self.assertEqual(improved.deterioration_score, 1.0)
        self.assertEqual(improved.deterioration_band, "stable")
        self.assertEqual(worsened.deterioration_score, 0.25)
        self.assertEqual(worsened.deterioration_band, "unstable")

    def test_constant_baseline_detects_change_and_does_not_claim_perfect_similarity(self):
        active = _baseline(stds={key: 0.0 for key in baseline.VARIABLES})
        result, _ = _compute(active, [_checkin(mood=3, craving_inv=3, self_efficacy=3)])
        self.assertLess(result.score, 1.0)
        self.assertEqual(result.z_scores["mood"], -2.0)
        self.assertEqual(result.effective_stds, baseline.STD_FLOORS)

    def test_sleep_changes_are_bilateral_and_never_marked_as_favourable(self):
        shorter, _ = _compute(_baseline(), [_checkin(sleep_hours=2)])
        longer, _ = _compute(_baseline(), [_checkin(sleep_hours=8)])
        self.assertEqual(shorter.score, longer.score)
        self.assertEqual(shorter.deterioration_score, longer.deterioration_score)
        self.assertEqual(longer.adverse_z_scores["sleep_hours"], 3.0)
        self.assertEqual(longer.favourable_composite_z, 0.0)

    def test_largest_valid_deviation_and_tiny_sd_still_have_a_positive_finite_score(self):
        active = _baseline(
            means={key: 0.0 for key in baseline.VARIABLES},
            stds={key: 1e-15 for key in baseline.VARIABLES},
        )
        result, _ = _compute(active, [_checkin(mood=10, craving_inv=10, sleep_hours=24, self_efficacy=10)])
        self.assertGreater(result.score, 0.0)
        self.assertTrue(math.isfinite(result.score))
        self.assertGreater(result.deterioration_score, 0.0)
        self.assertGreater(baseline._similarity(1e12), 0.0)

    def test_missing_recent_axis_is_unknown_not_zero_or_a_three_axis_average(self):
        result, db = _compute(_baseline(), [_checkin(self_efficacy=None)])
        self.assertIsNone(result.score)
        self.assertIsNone(result.deterioration_score)
        self.assertEqual(result.confidence_band, "insufficient_data")
        self.assertEqual(result.recent_counts["self_efficacy"], 0)
        self.assertNotIn("self_efficacy", result.z_scores)
        db.add.assert_not_called()

    def test_missing_or_invalid_baseline_axis_is_not_a_zero_z_score(self):
        for field, value in (("mean", None), ("mean", float("nan")), ("std", -1), ("std", float("inf")), ("n", 4)):
            with self.subTest(field=field, value=value):
                active = _baseline()
                active.stats["mood"][field] = value
                result, _ = _compute(active, [_checkin()])
                self.assertIsNone(result.score)
                self.assertNotIn("mood", result.z_scores)

    def test_malformed_baseline_json_axis_is_unknown(self):
        active = _baseline()
        active.stats["mood"] = "unavailable"
        result, _ = _compute(active, [_checkin()])
        self.assertIsNone(result.score)
        self.assertNotIn("mood", result.z_scores)

    def test_invalid_recent_values_are_excluded_but_observed_zero_is_preserved(self):
        rows = [_checkin(mood=None), _checkin(mood=float("nan")), _checkin(mood=11), _checkin(mood=True), _checkin(mood=0)]
        result, _ = _compute(_baseline(), rows)
        self.assertEqual(result.recent_counts["mood"], 1)
        self.assertEqual(result.recent_means["mood"], 0.0)
        self.assertEqual(result.z_scores["mood"], -5.0)
        self.assertIsNotNone(result.score)

    def test_identical_values_and_repeated_calculation_are_stable(self):
        active = _baseline()
        first, _ = _compute(active, [_checkin()] * 7)
        second, _ = _compute(active, [_checkin()] * 7)
        self.assertEqual(first, second)
        self.assertEqual(first.score, 1.0)
        self.assertEqual(first.deterioration_score, 1.0)
        self.assertEqual(first.confidence_band, "stable")

    def test_no_baseline_or_no_recent_data_never_produces_zero(self):
        for active, rows in ((None, [_checkin()]), (_baseline(), [])):
            with self.subTest(has_baseline=active is not None):
                result, _ = _compute(active, rows)
                self.assertIsNone(result.score)
                self.assertIsNone(result.deterioration_score)
                self.assertEqual(result.deterioration_band, "insufficient_data")

    def test_heuristic_bands_preserve_deviation_boundaries_not_old_score_cutoffs(self):
        self.assertEqual(baseline._deviation_band(1.2), "stable")
        self.assertEqual(baseline._deviation_band(1.20001), "transition")
        self.assertEqual(baseline._deviation_band(1.95), "transition")
        self.assertEqual(baseline._deviation_band(1.95001), "unstable")


class BaselineFreshnessTests(unittest.TestCase):
    def test_timezone_aware_baseline_dates_are_compared_in_utc(self):
        active = _baseline()
        active.window_end = datetime.now(timezone.utc) - timedelta(days=1)
        self.assertFalse(baseline._baseline_is_stale(active))

    def test_old_observations_are_refreshed_even_when_row_was_created_recently(self):
        active = _baseline(days_old=baseline.BASELINE_MAX_AGE_DAYS + 1)
        active.created_at = datetime.utcnow()
        replacement = _baseline()
        with patch.object(baseline, "get_active_baseline", return_value=active), \
             patch.object(baseline, "compute_or_refresh_baseline", return_value=replacement) as refresh:
            self.assertIs(baseline._current_baseline(MagicMock(), uuid.uuid4()), replacement)
        refresh.assert_called_once()

    def test_stale_fallback_is_explicit_in_result_and_persisted_signal(self):
        result, db = _compute(_baseline(days_old=baseline.BASELINE_MAX_AGE_DAYS + 1), [_checkin()])
        self.assertTrue(result.baseline_is_stale)
        self.assertTrue(db.add.call_args.args[0].value["baseline_is_stale"])

    def test_refresh_requires_five_observations_on_every_axis(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [_checkin(self_efficacy=None)] * 10
        self.assertIsNone(baseline.compute_or_refresh_baseline(db, uuid.uuid4()))
        db.add.assert_not_called()
        db.query.return_value.filter.return_value.update.assert_not_called()

    def test_refresh_replaces_active_baseline_only_after_complete_stats_exist(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [_checkin()] * 5
        result = baseline.compute_or_refresh_baseline(db, uuid.uuid4())
        self.assertIsInstance(result, Baseline)
        self.assertTrue(result.is_active)
        self.assertEqual(result.stats["mood"], {"mean": 5.0, "std": 0.0, "n": 5})
        db.query.return_value.filter.return_value.update.assert_called_once_with({"is_active": False})
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
