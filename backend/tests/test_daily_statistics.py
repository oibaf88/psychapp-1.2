"""Synthetic arithmetic/lineage regressions; no patient data or DB needed."""
import json
import math
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.daily_statistics import (
    VARIABLES,
    aggregate_daily_statistics,
    local_day,
    window_bounds,
)

NOW = datetime(2026, 8, 31, 18)


def checkin(day=30, hour=12, **values):
    fields = {"mood": None, "craving": None, "sleep_hours": None, "self_efficacy": None}
    return SimpleNamespace(created_at=datetime(2026, 8, day, hour), **(fields | values))


def signal(day=30, hour=12, *, at=None, trace=None, active=True, **value):
    return SimpleNamespace(id=uuid.uuid4(), timestamp=at or datetime(2026, 8, day, hour), value=value, agent2_trace_id=trace.id if trace else None, is_active=active, superseded_by_fact=None)


def trace(source=None):
    return SimpleNamespace(id=uuid.uuid4(), source_type="chat_message", chat_message_id=source or uuid.uuid4(), diary_entry_id=None)


def observation(day=30, **values):
    fields = dict(id=uuid.uuid4(), observed_at=datetime(2026, 8, day, 12), created_at=datetime(2026, 8, day, 12), source_type="diary_entry", diary_entry_id=uuid.uuid4(), chat_message_id=None, trace_id=None, domain="housing", category="housing_insecure", valence="risk", intensity=0.8, confidence=0.9, is_change=True, status="inferred", summary="PRIVATE SUMMARY", evidence_quote="PRIVATE QUOTE")
    return SimpleNamespace(**(fields | values))


def calculate(checkins=(), signals=(), observations=(), **kwargs):
    return aggregate_daily_statistics(checkins, signals, observations, window_days=7, now=NOW, **kwargs)


def pair(result, x, y):
    return next(item for item in result["correlations"] if {item["x"], item["y"]} == {x, y})


class DailyStatisticsArithmeticTests(unittest.TestCase):
    def test_daily_means_then_equal_day_weight_not_equal_record_weight(self):
        result = calculate([checkin(29, mood=0), checkin(29, hour=13, mood=2), checkin(29, hour=14, mood=4), checkin(30, mood=10)])
        self.assertEqual([row["mood"] for row in result["daily"]], [2.0, 10.0])
        self.assertEqual(result["summary"]["mood"]["mean"], 6.0)
        self.assertEqual(result["summary"]["mood"]["n"], 2)
        self.assertAlmostEqual(result["summary"]["mood"]["sd"], math.sqrt(32))
        self.assertEqual(result["daily"][0]["statistics"]["mood"]["sd"], 2.0)
        self.assertEqual(result["daily"][0]["counts"]["checkins"], 3)

    def test_zero_is_valid_missing_is_null_and_nonfinite_is_excluded(self):
        result = calculate([checkin(mood=0, craving=0, sleep_hours=0, self_efficacy=0), checkin(hour=13, mood=float("nan"), craving=float("inf"), sleep_hours=True, self_efficacy="5")])
        row = result["daily"][0]
        for key in ("mood", "craving", "sleep_hours", "self_efficacy"):
            self.assertEqual(row[key], 0)
            self.assertEqual(row["statistics"][key]["n"], 1)
            self.assertEqual(row["statistics"][key]["missing_count"], 1)
            self.assertIsNone(row["statistics"][key]["sd"])
        self.assertIsNone(row["negative_valence"])
        self.assertIsNone(row["ideation"])
        self.assertEqual(result["summary"]["negative_valence"]["n"], 0)
        json.dumps(result, allow_nan=False)

    def test_daily_interaction_valence_has_its_own_denominator(self):
        signals = [signal(negative_valence=0), signal(hour=13, negative_valence=1), signal(hour=14, rumination_score=0.7)]
        row = calculate([checkin(mood=4), checkin(hour=14, mood=6)], signals)["daily"][0]
        self.assertEqual(row["interaction_valence_mean"], 0.5)
        self.assertEqual(row["negative_valence"], 0.5)
        self.assertEqual(row["statistics"]["interaction_valence_mean"]["n"], 2)
        self.assertEqual(row["statistics"]["interaction_valence_mean"]["missing_count"], 1)
        self.assertEqual(row["counts"]["interactions"], 3)
        self.assertEqual(row["mood"], 5)

    def test_ideation_cannot_be_diluted_by_many_other_interactions(self):
        signals = [signal(hour=hour, ideation_direct=False, ideation_indirect=False) for hour in range(9)]
        signals.append(signal(hour=10, ideation_indirect=True, ideation_direct=False))
        result = calculate(signals=signals)
        row = result["daily"][0]
        self.assertIs(row["ideation"], True)
        self.assertIs(row["ideation_indirect"], True)
        self.assertEqual(row["statistics"]["ideation"]["true_count"], 1)
        self.assertEqual(row["statistics"]["ideation"]["false_count"], 9)
        self.assertEqual(result["summary"]["ideation"]["rate"], 1)
        self.assertEqual(result["summary"]["ideation"]["n"], 1)

    def test_missing_boolean_and_false_are_distinct(self):
        result = calculate(signals=[signal(ideation_direct=False, consumption_crisis="false")])
        row = result["daily"][0]
        self.assertIs(row["ideation_direct"], False)
        self.assertIsNone(row["ideation_indirect"])
        self.assertIsNone(row["ideation"])
        self.assertIsNone(row["consumption_crisis"])

    def test_psychosocial_content_uses_persisted_flag_not_observation_count(self):
        result = calculate(signals=[signal(28), signal(29, has_psychosocial_content=False), signal(30, has_psychosocial_content=True)])
        self.assertIsNone(result["daily"][0]["has_psychosocial_content"])
        self.assertIs(result["daily"][1]["has_psychosocial_content"], False)
        self.assertIs(result["daily"][2]["has_psychosocial_content"], True)
        self.assertEqual(result["summary"]["has_psychosocial_content"]["n"], 2)
        self.assertEqual(result["summary"]["has_psychosocial_content"]["true_count"], 1)
        self.assertEqual(result["summary"]["has_psychosocial_content"]["missing_days"], 5)

    def test_all_linguistic_schema_fields_are_present_except_free_text(self):
        from app.content.prompts import ANALYZER_TOOL_SCHEMA
        fields = set(ANALYZER_TOOL_SCHEMA["input_schema"]["properties"]["linguistic"]["properties"])
        represented = {item["key"] for item in VARIABLES}
        self.assertEqual(fields - represented, {"short_rationale"})

    def test_categories_are_counted_without_inventing_a_numeric_scale(self):
        signals = [signal(emotional_complexity="high", deviation_from_own_baseline="unknown"), signal(hour=13, emotional_complexity="low"), signal(31, emotional_complexity="high")]
        result = calculate(signals=signals)
        summary = result["summary"]["emotional_complexity"]
        self.assertEqual(summary["counts"], {"high": 2, "low": 1})
        self.assertEqual(summary["day_counts"], {"high": 2, "low": 1})
        self.assertEqual(summary["observed_days"], 2)
        self.assertEqual(summary["n"], 3)
        self.assertNotIn("mean", summary)
        self.assertFalse(any("emotional_complexity" in (p["x"], p["y"]) for p in result["correlations"]))


class DailyStatisticsTimeAndLineageTests(unittest.TestCase):
    def test_timezone_changes_date_without_merging_utc_calendar_days(self):
        rows = [checkin(30, hour=21, mood=2), checkin(30, hour=22, mood=8)]
        result = calculate(rows)
        self.assertEqual([r["date"] for r in result["daily"]], ["2026-08-30", "2026-08-31"])
        self.assertEqual(local_day(datetime(2026, 1, 30, 23)), "2026-01-31")
        self.assertEqual(result["timezone"], "Europe/Madrid")

    def test_madrid_day_handles_spring_and_autumn_clock_changes(self):
        start, end = window_bounds(1, datetime(2026, 3, 29, 22, tzinfo=timezone.utc) - timedelta(microseconds=1))
        self.assertEqual(start, datetime(2026, 3, 28, 23))
        self.assertEqual(end - start + timedelta(microseconds=1), timedelta(hours=23))
        start, end = window_bounds(1, datetime(2026, 10, 25, 23, tzinfo=timezone.utc) - timedelta(microseconds=1))
        self.assertEqual(start, datetime(2026, 10, 24, 22))
        self.assertEqual(end - start + timedelta(microseconds=1), timedelta(hours=25))

    def test_source_day_wins_over_next_day_analysis_time(self):
        source = trace()
        result = calculate(signals=[signal(31, trace=source, negative_valence=0.6)], traces_by_id={source.id: source}, source_times={("chat_message", str(source.chat_message_id)): datetime(2026, 8, 30, 12)})
        self.assertEqual(result["daily"][0]["date"], "2026-08-30")
        self.assertEqual(result["provenance"]["interaction_timestamp_fallbacks"], 0)

    def test_retry_does_not_double_weight_and_latest_refutation_stays_excluded(self):
        older, newer = trace(), trace()
        newer.chat_message_id = older.chat_message_id
        traces = {older.id: older, newer.id: newer}
        signals = [signal(trace=older, negative_valence=0.1), signal(hour=13, trace=newer, negative_valence=0.9)]
        result = calculate(signals=signals, traces_by_id=traces)
        self.assertEqual(result["daily"][0]["interaction_valence_mean"], 0.9)
        self.assertEqual(result["daily"][0]["counts"]["interactions"], 1)
        self.assertEqual(result["provenance"]["excluded_duplicate_analyses"], 1)
        signals[-1].is_active = False
        result = calculate(signals=signals, traces_by_id=traces)
        self.assertEqual(result["daily"], [])
        self.assertEqual(result["provenance"]["excluded_refuted_signals"], 1)

    def test_psychosocial_refutations_are_excluded_and_free_text_never_leaks(self):
        rows = [observation(intensity=0, confidence=0.6, is_change=False), observation(intensity=1, confidence=1), observation(status="refuted", intensity=1)]
        result = calculate(observations=rows)
        row = result["daily"][0]
        self.assertEqual(row["counts"]["psychosocial_observations"], 2)
        self.assertEqual(row["psychosocial_intensity_mean"], 0.5)
        self.assertEqual(row["psychosocial_confidence_mean"], 0.8)
        self.assertIs(row["psychosocial_is_change"], True)
        self.assertEqual(row["categories"]["psychosocial_valence"]["counts"], {"risk": 2})
        self.assertEqual(result["provenance"]["excluded_refuted_observations"], 1)
        encoded = json.dumps(result)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn(str(rows[0].id), encoded)

    def test_window_excludes_old_and_future_data_and_preserves_missing_days(self):
        result = calculate([checkin(24, mood=9), checkin(25, mood=2), checkin(31, hour=19, mood=8)])
        self.assertEqual(len(result["daily"]), 1)
        self.assertEqual(result["start_date"], "2026-08-25")
        self.assertEqual(result["end_date"], "2026-08-31")
        self.assertEqual(result["summary"]["mood"]["n"], 1)
        self.assertEqual(result["summary"]["mood"]["missing_days"], 6)

    def test_psychosocial_retry_keeps_one_observation_per_domain_and_text(self):
        source = uuid.uuid4()
        rows = [observation(diary_entry_id=source, intensity=0.1), observation(diary_entry_id=source, intensity=0.9, created_at=datetime(2026, 8, 30, 13))]
        result = calculate(observations=rows)
        self.assertEqual(result["daily"][0]["psychosocial_intensity_mean"], 0.9)
        self.assertEqual(result["daily"][0]["counts"]["psychosocial_observations"], 1)
        self.assertEqual(result["provenance"]["excluded_duplicate_observations"], 1)


class DailyStatisticsCorrelationTests(unittest.TestCase):
    def test_pearson_uses_complete_same_day_pairs_only(self):
        checkins = [checkin(26, mood=0, sleep_hours=2), checkin(27, mood=2, sleep_hours=4), checkin(28, mood=4, sleep_hours=6), checkin(29, mood=10), checkin(30, sleep_hours=0)]
        result = calculate(checkins)
        p = pair(result, "mood", "sleep_hours")
        self.assertEqual(p["n"], 3)
        self.assertAlmostEqual(p["r"], 1)
        self.assertEqual(p["status"], "ok")

    def test_too_few_pairs_and_constant_series_are_not_fake_zero_correlations(self):
        result = calculate([checkin(28, mood=2, sleep_hours=6, craving=3), checkin(29, mood=4, sleep_hours=6, craving=2), checkin(30, mood=8, sleep_hours=6)])
        self.assertEqual(pair(result, "mood", "sleep_hours")["status"], "constant_series")
        self.assertIsNone(pair(result, "mood", "sleep_hours")["r"])
        self.assertEqual(pair(result, "mood", "craving")["status"], "insufficient_pairs")
        self.assertEqual(pair(result, "mood", "craving")["n"], 2)
        self.assertIsNone(pair(result, "mood", "craving")["r"])

    def test_boolean_days_can_pair_with_continuous_data_without_dilution(self):
        checkins = [checkin(28, mood=0), checkin(29, mood=0), checkin(30, mood=10)]
        signals = [signal(28, ideation_direct=True), signal(29, ideation_indirect=True), signal(30, ideation_direct=False, ideation_indirect=False)]
        p = pair(calculate(checkins, signals), "mood", "ideation")
        self.assertEqual(p["n"], 3)
        self.assertAlmostEqual(p["r"], -1)

    def test_empty_window_returns_explicit_missing_statistics(self):
        result = calculate()
        self.assertEqual(result["daily"], [])
        self.assertIsNone(result["summary"]["mood"]["mean"])
        self.assertEqual(result["summary"]["mood"]["missing_days"], 7)
        self.assertTrue(all(p["r"] is None and p["n"] == 0 for p in result["correlations"]))


if __name__ == "__main__":
    unittest.main()
