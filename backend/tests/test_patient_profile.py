"""Judging each patient against themselves, and the fallback when we cannot.

The reconfiguration this belongs to started from a false positive: someone
saying they had decided to change their life for the better was treated as a
suicide crisis. One cause was that the analytic layer had no idea who was
writing. `rumination_score > 0.60` was the same threshold for a person who
writes in long anxious spirals and one who answers in four words.

The most important test in this file is not any of the personal-baseline
ones. It is `NoProfileFallbackTests`: a patient the system has never met has
to be evaluated exactly as they were before profiles existed. A safety layer
that only works once it knows you is not a safety layer.
"""
import unittest
import unittest.mock
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

from app.models import PatientProfile
from app.services import profile as profile_service


def _profile(baseline=None, n=0, portrait=None, threads=None, edited_by=None):
    return SimpleNamespace(
        linguistic_baseline=baseline,
        linguistic_baseline_n=n,
        linguistic_baseline_updated_at=datetime.utcnow(),
        portrait=portrait,
        previous_portrait=None,
        portrait_version=1 if portrait else 0,
        portrait_edited_by=edited_by,
        open_threads=threads,
    )


def _stats(mean, std, n=30):
    return {"mean": mean, "std": std, "n": n}


class NoProfileFallbackTests(unittest.TestCase):
    """A patient with no history must stay assessable."""

    def test_no_profile_reports_insufficient_data(self):
        d = profile_service.deviation(None, "rumination_score", 0.9)
        self.assertTrue(d.insufficient_data)
        self.assertIsNone(d.z)

    def test_a_z_of_zero_is_never_confused_with_no_data(self):
        """0.0 means 'exactly average for them'; absent means 'we do not know'.

        Collapsing the two would silently disarm every relative rule for
        every new patient, and it would look like the rules were working.
        """
        known = _profile({"rumination_score": _stats(0.5, 0.1)}, n=30)
        exactly_average = profile_service.deviation(known, "rumination_score", 0.5)
        self.assertEqual(exactly_average.z, 0.0)
        self.assertFalse(exactly_average.insufficient_data)

    def test_too_few_signals_is_insufficient_data(self):
        thin = _profile({"rumination_score": _stats(0.5, 0.1)}, n=3)
        self.assertTrue(profile_service.deviation(thin, "rumination_score", 0.9).insufficient_data)

    def test_an_axis_that_was_never_scored_is_insufficient_data(self):
        partial = _profile({"rumination_score": _stats(0.5, 0.1)}, n=30)
        self.assertTrue(profile_service.deviation(partial, "ambivalence", 0.9).insufficient_data)

    def test_a_flat_baseline_is_insufficient_data(self):
        """Someone who always scores the same has no spread to divide by."""
        flat = _profile({"rumination_score": _stats(0.5, 0.0)}, n=30)
        d = profile_service.deviation(flat, "rumination_score", 0.9)
        self.assertTrue(d.insufficient_data)
        self.assertIsNone(d.z)

    def test_a_non_numeric_reading_is_insufficient_data(self):
        known = _profile({"rumination_score": _stats(0.5, 0.1)}, n=30)
        for value in (None, "alto", True):
            self.assertTrue(profile_service.deviation(known, "rumination_score", value).insufficient_data)


class DeviationTests(unittest.TestCase):
    def test_the_same_score_means_opposite_things_for_two_people(self):
        """The whole point, in one test."""
        spiraller = _profile({"rumination_score": _stats(0.70, 0.08)}, n=30)
        terse = _profile({"rumination_score": _stats(0.15, 0.08)}, n=30)

        # 0.72 is above the absolute 0.60 threshold for both.
        self.assertAlmostEqual(profile_service.deviation(spiraller, "rumination_score", 0.72).z, 0.25, places=2)
        self.assertGreater(profile_service.deviation(terse, "rumination_score", 0.72).z, 6)

    def test_a_reading_below_their_normal_gives_a_negative_z(self):
        p = _profile({"negative_valence": _stats(0.60, 0.10)}, n=30)
        self.assertLess(profile_service.deviation(p, "negative_valence", 0.30).z, 0)


class LinguisticStatsTests(unittest.TestCase):
    class _Db:
        def __init__(self, signals):
            self.signals = signals

        def query(self, _model):
            return self

        def filter(self, *_a):
            return self

        def all(self):
            return self.signals

    def _signal(self, **values):
        return SimpleNamespace(value=values, timestamp=datetime.utcnow())

    def test_stats_are_computed_per_axis(self):
        db = self._Db([
            self._signal(rumination_score=0.2, negative_valence=0.4, urgency_level=0.1, ambivalence=0.5),
            self._signal(rumination_score=0.4, negative_valence=0.6, urgency_level=0.3, ambivalence=0.5),
        ])
        stats, n = profile_service.compute_linguistic_stats(db, uuid.uuid4())
        self.assertAlmostEqual(stats["rumination_score"]["mean"], 0.3, places=3)
        self.assertEqual(n, 2)

    def test_booleans_are_not_averaged_into_the_baseline(self):
        """"Have they expressed ideation" is not more or less true on average."""
        self.assertNotIn("ideation_direct", profile_service.LINGUISTIC_VARIABLES)
        self.assertNotIn("ideation_indirect", profile_service.LINGUISTIC_VARIABLES)
        db = self._Db([self._signal(rumination_score=0.2, ideation_direct=True)])
        stats, _n = profile_service.compute_linguistic_stats(db, uuid.uuid4())
        self.assertNotIn("ideation_direct", stats)

    def test_a_boolean_in_a_numeric_field_is_not_counted(self):
        """`True` is an int in Python, and would average in as 1.0."""
        db = self._Db([
            self._signal(rumination_score=0.2),
            self._signal(rumination_score=True),
        ])
        stats, _n = profile_service.compute_linguistic_stats(db, uuid.uuid4())
        self.assertEqual(stats["rumination_score"]["n"], 1)

    def test_n_is_the_thinnest_axis_not_the_richest(self):
        """Otherwise the engine compares against a mean built from one point."""
        db = self._Db([
            self._signal(rumination_score=0.2, negative_valence=0.4, urgency_level=0.1, ambivalence=0.5),
            self._signal(rumination_score=0.4),
        ])
        _stats_out, n = profile_service.compute_linguistic_stats(db, uuid.uuid4())
        self.assertEqual(n, 1)

    def test_the_query_excludes_inactive_signals(self):
        """A refuted signal must stop defining what is normal for the person."""
        import inspect

        source = inspect.getsource(profile_service.compute_linguistic_stats)
        self.assertIn("AlfaSignal.is_active", source)


class ThreadHygieneTests(unittest.TestCase):
    def test_duplicate_topics_collapse(self):
        cleaned = profile_service._clean_threads([
            {"topic": "El piso", "note": "a"},
            {"topic": "el piso", "note": "b"},
        ])
        self.assertEqual(len(cleaned), 1)

    def test_the_agenda_is_bounded(self):
        cleaned = profile_service._clean_threads(
            [{"topic": f"tema {i}"} for i in range(50)]
        )
        self.assertLessEqual(len(cleaned), profile_service.MAX_OPEN_THREADS)

    def test_junk_is_dropped_rather_than_crashing(self):
        self.assertEqual(profile_service._clean_threads("no soy una lista"), [])
        self.assertEqual(profile_service._clean_threads([None, 3, {"note": "sin tema"}]), [])

    def test_long_text_is_truncated_not_rejected(self):
        cleaned = profile_service._clean_threads([{"topic": "x" * 500, "note": "y" * 5000}])
        self.assertEqual(len(cleaned[0]["topic"]), profile_service.MAX_THREAD_TOPIC_CHARS)
        self.assertEqual(len(cleaned[0]["note"]), profile_service.MAX_THREAD_NOTE_CHARS)


class AnalyzerContextTests(unittest.TestCase):
    def test_an_empty_profile_contributes_no_prompt_section(self):
        """A section announcing its own emptiness reads as a fact about the patient."""
        self.assertEqual(profile_service.analyzer_context_block(None), "")
        self.assertEqual(profile_service.analyzer_context_block(_profile()), "")

    def test_the_baseline_is_withheld_until_it_means_something(self):
        thin = _profile({"rumination_score": _stats(0.5, 0.1)}, n=2)
        self.assertNotIn("habitualmente", profile_service.analyzer_context_block(thin))

    def test_the_portrait_says_who_wrote_it(self):
        clinician = _profile(portrait="Vive sola.", edited_by=uuid.uuid4())
        model = _profile(portrait="Vive sola.")
        self.assertIn("corregido por el profesional", profile_service.analyzer_context_block(clinician))
        self.assertIn("acumulado por el sistema", profile_service.analyzer_context_block(model))

    def test_the_baseline_block_states_the_comparison_to_make(self):
        rich = _profile({"rumination_score": _stats(0.70, 0.08)}, n=30)
        block = profile_service.analyzer_context_block(rich)
        self.assertIn("0.70", block)
        self.assertIn("no es una señal", block)


class ProfileColumnTests(unittest.TestCase):
    def test_one_row_per_patient_is_enforced_by_the_database(self):
        """Two profiles would mean two answers to "what is normal for them"."""
        self.assertTrue(PatientProfile.__table__.columns["user_id"].unique)

    def test_the_migration_matches_the_orm(self):
        import pathlib

        migrations = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
        sql = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.sql"))
        self.assertIn("create table if not exists psychdeep_v12.patient_profiles", sql)
        for column in PatientProfile.__table__.columns.keys():
            self.assertIn(column, sql, f"{column} has no matching migration column")

    def test_the_table_is_hardened_like_the_clinical_tables(self):
        """It holds a model-written portrait of a patient in treatment."""
        import pathlib

        migrations = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
        sql = next(migrations.glob("*add_patient_profiles.sql")).read_text(encoding="utf-8")
        self.assertIn("force row level security", sql)
        self.assertIn("create policy backend_full_access", sql)
        self.assertIn("revoke all on table psychdeep_v12.patient_profiles", sql)


class UnfrozenBaselineTests(unittest.TestCase):
    """The check-in baseline used to be created once and never revisited."""

    def test_a_fresh_baseline_is_reused(self):
        from app.services import baseline

        active = SimpleNamespace(created_at=datetime.utcnow() - timedelta(days=1))
        db = SimpleNamespace()
        with unittest.mock.patch.object(baseline, "get_active_baseline", return_value=active), \
             unittest.mock.patch.object(baseline, "compute_or_refresh_baseline") as recompute:
            self.assertIs(baseline._current_baseline(db, uuid.uuid4()), active)
        recompute.assert_not_called()

    def test_a_stale_baseline_is_recomputed(self):
        from app.services import baseline

        stale = SimpleNamespace(
            created_at=datetime.utcnow() - timedelta(days=baseline.BASELINE_MAX_AGE_DAYS + 1)
        )
        fresh = SimpleNamespace(created_at=datetime.utcnow())
        db = SimpleNamespace()
        with unittest.mock.patch.object(baseline, "get_active_baseline", return_value=stale), \
             unittest.mock.patch.object(baseline, "compute_or_refresh_baseline", return_value=fresh):
            self.assertIs(baseline._current_baseline(db, uuid.uuid4()), fresh)

    def test_a_stale_baseline_survives_a_failed_recompute(self):
        """Losing a baseline because someone stopped checking in would take
        the structural axis offline exactly when it is worth watching."""
        from app.services import baseline

        stale = SimpleNamespace(
            created_at=datetime.utcnow() - timedelta(days=baseline.BASELINE_MAX_AGE_DAYS + 1)
        )
        db = SimpleNamespace()
        with unittest.mock.patch.object(baseline, "get_active_baseline", return_value=stale), \
             unittest.mock.patch.object(baseline, "compute_or_refresh_baseline", return_value=None):
            self.assertIs(baseline._current_baseline(db, uuid.uuid4()), stale)


if __name__ == "__main__":
    unittest.main()


class RelativeThresholdTests(unittest.TestCase):
    """The engine asking "is this unusual for them?" without ever asking it
    instead of "is this high?".

    The predicate is an OR on purpose. Replacing the absolute threshold with
    a relative one would mean a patient whose baseline is genuinely alarming
    stops tripping anything precisely because it has become normal for them
    — which is the worst possible way for a safety system to fail.
    """

    def _flags(self, *, rumination, baseline_mean=None, baseline_std=0.08, n=30):
        """Recreate the engine's two predicates over one reading."""
        from app.services import risk_engine

        prof = (
            None
            if baseline_mean is None
            else _profile({"rumination_score": _stats(baseline_mean, baseline_std)}, n=n)
        )
        dev = profile_service.deviation(prof, "rumination_score", rumination)
        absolute = rumination > risk_engine.SUBTLE_RUMINATION_MIN
        unusual = (
            not dev.insufficient_data
            and dev.z is not None
            and dev.z > risk_engine.PERSONAL_DEVIATION_SIGMA
        )
        return absolute, unusual, absolute or unusual

    def test_a_high_reading_still_trips_without_any_baseline(self):
        absolute, unusual, high = self._flags(rumination=0.75)
        self.assertTrue(absolute)
        self.assertFalse(unusual)
        self.assertTrue(high)

    def test_a_low_reading_still_does_not_trip_without_a_baseline(self):
        _absolute, _unusual, high = self._flags(rumination=0.30)
        self.assertFalse(high)

    def test_a_spike_below_the_constant_now_trips_for_a_quiet_writer(self):
        """0.45 is nothing in absolute terms, and three sigma for this person."""
        absolute, unusual, high = self._flags(rumination=0.45, baseline_mean=0.15)
        self.assertFalse(absolute)
        self.assertTrue(unusual)
        self.assertTrue(high)

    def test_an_alarming_baseline_does_not_become_invisible(self):
        """This is the failure mode a pure relative rule would introduce."""
        absolute, unusual, high = self._flags(rumination=0.75, baseline_mean=0.74)
        self.assertTrue(absolute)
        self.assertFalse(unusual, "0.75 is ordinary for them...")
        self.assertTrue(high, "...but it is still high, and must still count")

    def test_an_ordinary_message_from_a_spiraller_stops_tripping_on_nothing(self):
        """The false-positive direction: high for everyone, normal for them.

        The absolute threshold still fires — deliberately, since it is the
        floor — but the trace now records that it was typical for this
        person, which is what a therapist needs to judge the alert.
        """
        absolute, unusual, _high = self._flags(rumination=0.72, baseline_mean=0.70)
        self.assertTrue(absolute)
        self.assertFalse(unusual)

    def test_a_thin_baseline_falls_back_to_the_constant_alone(self):
        _absolute, unusual, high = self._flags(rumination=0.45, baseline_mean=0.15, n=3)
        self.assertFalse(unusual)
        self.assertFalse(high)


class NeverRaisesIntoTheChatTests(unittest.TestCase):
    """The analysis path promises the patient-facing flow survives it."""

    def test_a_broken_profile_layer_does_not_break_the_analysis(self):
        from app.services import conversation

        db = unittest.mock.MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        trace = SimpleNamespace(id=uuid.uuid4(), status="started", error_code=None)
        result = SimpleNamespace(
            value={
                "linguistic": {
                    "rumination_score": 0.2,
                    "negative_valence": 0.3,
                    "urgency_level": 0.1,
                    "ideation_indirect": False,
                    "ideation_direct": False,
                    "consumption_crisis": False,
                    "ambivalence": 0.4,
                    "emotional_complexity": "low",
                    "short_rationale": "Nada reseñable.",
                },
                "psychosocial": {"has_psychosocial_content": False, "observations": []},
            },
            metadata=SimpleNamespace(provider="anthropic", requested_model="m"),
        )
        provider = SimpleNamespace(analyze_structured=lambda *_a, **_k: result)

        with unittest.mock.patch.object(conversation.agent2_trace, "start", return_value=trace), \
             unittest.mock.patch.object(conversation.agent2_trace, "mark_succeeded", lambda *_a, **_k: None), \
             unittest.mock.patch.object(conversation, "get_llm_provider", return_value=provider), \
             unittest.mock.patch.object(
                 conversation.profile_service,
                 "refresh_linguistic_baseline",
                 side_effect=RuntimeError("patient_profiles does not exist"),
             ):
            outcome = conversation.analyze_text_and_store(
                db,
                uuid.uuid4(),
                "Hoy he dormido fatal y llevo toda la semana dándole vueltas.",
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        # The signal the risk engine reads still landed.
        self.assertEqual(outcome.status, "succeeded")

    def test_the_startup_check_would_catch_a_missing_table(self):
        """Better to refuse to boot than to 500 on every patient message."""
        import inspect

        from app import main

        source = inspect.getsource(main)
        self.assertIn('("patient_profiles", "id")', source)
