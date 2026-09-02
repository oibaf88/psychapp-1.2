"""Regression scenarios: safety priority cannot be averaged away."""
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models import AlfaSignal

from app.services import risk_engine
from test_risk_traceability import _CalculationHarness, _linguistic, _structural


class SafetyPriorityTests(_CalculationHarness, unittest.TestCase):
    def test_indirect_ideation_alone_requires_priority_review(self):
        result = self._calculate(
            structural=_structural(score=1, band="stable"),
            linguistic=_linguistic(ideation_indirect=True, rumination=0, negative_valence=0),
        )
        self.assertEqual(result.level, 3)
        self.assertEqual(result.triggering_rules, ["N3_senal_linguistica_ideacion_indirecta"])
        self.assertIn("no ideación confirmada", result.reason)

    def test_indirect_ideation_does_not_need_a_structural_baseline(self):
        result = self._calculate(
            structural=replace(_structural(), score=None, confidence_band="insufficient_data", deterioration_band="insufficient_data", adverse_composite_z=None),
            linguistic=_linguistic(ideation_indirect=True),
        )
        self.assertEqual(result.level, 3)

    def test_improvement_does_not_trigger_deterioration_rules(self):
        result = self._calculate(
            structural=replace(_structural(score=.1, band="unstable"), deterioration_band="stable", deterioration_score=1, adverse_composite_z=0),
            linguistic=_linguistic(rumination=.1), persistence=(1, 3, 5),
        )
        self.assertEqual(result.level, 0)
        self.assertEqual(result.input_signals["confidence_band"], "unstable")
        self.assertEqual(result.input_signals["deterioration_band"], "stable")

    def test_statistical_convergence_alone_is_not_suicide_emergency(self):
        result = self._calculate(
            structural=_structural(score=.1, band="unstable"),
            linguistic=_linguistic(rumination=.95), persistence=(1, 3, 5),
        )
        self.assertEqual(result.level, 3)
        self.assertEqual(result.triggering_rules, ["N3_convergencia_critica_extrema"])

    def test_explicit_safety_signal_retains_priority_over_statistics(self):
        result = self._calculate(
            structural=_structural(score=.1, band="unstable"),
            linguistic=_linguistic(ideation=True, ideation_indirect=True, rumination=.95),
        )
        self.assertEqual(result.level, 4)

    def test_neutral_message_keeps_recent_unrefuted_signal_and_exact_lineage(self):
        current = uuid.uuid4()
        previous = str(uuid.uuid4())
        safety = {
            "window_hours": 12, "ideation_direct": False,
            "ideation_indirect": True, "consumption_crisis": False,
            "evidence": [{"signal_id": previous, "ideation_indirect": True}],
        }
        with patch.object(risk_engine, "_recent_safety_signals", return_value=safety):
            result = self._calculate(linguistic=_linguistic(signal_id=current, rumination=0), preferred_signal_id=current)
        self.assertEqual(result.level, 3)
        self.assertEqual(result.linguistic_signal_id, current)
        self.assertEqual(result.input_signals["safety_review"]["evidence"][0]["signal_id"], previous)


class SafetyQueryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        AlfaSignal.__table__.create(self.engine)
        self.db = Session(self.engine)
        self.patient = uuid.uuid4()
        self.now = datetime.utcnow()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def signal(self, *, patient=None, hours=0, active=True, value=None, kind="linguistic_analysis", band=None):
        signal = AlfaSignal(user_id=patient or self.patient, signal_type=kind,
                            timestamp=self.now - timedelta(hours=hours), is_active=active,
                            value=value or {"ideation_indirect": True}, confidence_band=band)
        self.db.add(signal)
        self.db.commit()
        return signal

    def test_safety_overlay_excludes_other_patients_refutations_old_and_future_text(self):
        included = self.signal(hours=1)
        self.signal(patient=uuid.uuid4())
        self.signal(active=False)
        self.signal(hours=13)
        self.signal(hours=-1)
        self.signal(hours=.5, value={"ideation_indirect": False})
        result = risk_engine._recent_safety_signals(self.db, self.patient, now=self.now)
        self.assertTrue(result["ideation_indirect"])
        self.assertEqual([row["signal_id"] for row in result["evidence"]], [str(included.id)])

    def test_main_signal_query_excludes_other_patients_refutations_old_and_future_text(self):
        included = self.signal(hours=1)
        excluded = [
            self.signal(patient=uuid.uuid4()),
            self.signal(hours=.1, active=False),
            self.signal(hours=13),
            self.signal(hours=-1),
        ]
        result = risk_engine._linguistic_flags(self.db, self.patient, now=self.now)
        self.assertTrue(result["eligible_for_risk"])
        self.assertEqual(result["signal_id"], str(included.id))
        for row in excluded:
            with self.subTest(signal_id=str(row.id)):
                pinned = risk_engine._linguistic_flags(self.db, self.patient, signal_id=row.id, now=self.now)
                self.assertFalse(pinned["eligible_for_risk"])
                self.assertFalse(pinned["ideation_indirect"])

    def test_both_signal_routes_include_boundaries_and_exclude_future_microsecond(self):
        lower = self.signal(hours=12)
        upper = self.signal(hours=0)
        future = self.signal(hours=-1)
        future.timestamp = self.now + timedelta(microseconds=1)
        self.db.commit()
        main = risk_engine._linguistic_flags(self.db, self.patient, now=self.now)
        overlay = risk_engine._recent_safety_signals(self.db, self.patient, now=self.now)
        self.assertEqual(main["signal_id"], str(upper.id))
        self.assertEqual({row["signal_id"] for row in overlay["evidence"]}, {str(lower.id), str(upper.id)})
        self.assertFalse(risk_engine._linguistic_flags(self.db, self.patient, signal_id=future.id, now=self.now)["eligible_for_risk"])

    def test_persistence_never_counts_legacy_symmetric_score_as_deterioration(self):
        self.signal(hours=2, kind="structural_score", band="unstable", value={"score": 0})
        result = risk_engine._persistence_detail(self.db, self.patient, "unstable", 1)
        self.assertFalse(result["passed"])
        self.signal(hours=1, kind="structural_score", band="unstable", value={
            "score": .1, "calculation_version": "structural-v2", "deterioration_band": "stable",
        })
        self.assertFalse(risk_engine._persistence_detail(self.db, self.patient, "unstable", 1)["passed"])


if __name__ == "__main__":
    unittest.main()
