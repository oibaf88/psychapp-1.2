import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.models import CheckIn, PsychosocialObservation
from app.schemas import RiskAssessmentOut
from app.services import baseline, conversation, risk_engine
from app.services.conversation import AnalysisOutcome, LinguisticAnalysis
from app.services.llm.anthropic_provider import AnthropicProvider


VALID_ANALYSIS = {
    "rumination_score": 0.7,
    "negative_valence": 0.6,
    "urgency_level": 0.2,
    "ideation_indirect": False,
    "ideation_direct": False,
    "consumption_crisis": False,
    "ambivalence": 0.5,
    "emotional_complexity": "medium",
    "short_rationale": "Se observa rumiación moderada.",
}


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _limit):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeDb:
    """Model-aware stub: the engine reads check-ins and psychosocial rows.

    Returning check-ins for every model would make the psychosocial profile
    try to read a domain off a CheckIn, so the mapping is explicit and any
    unexpected model comes back empty.
    """

    def __init__(self, checkins=None, psychosocial=None):
        self.rows_by_model = {
            CheckIn: checkins or [],
            PsychosocialObservation: psychosocial or [],
        }

    def query(self, model):
        return _FakeQuery(self.rows_by_model.get(model, []))


def _structural(score=0.8, band="stable"):
    return baseline.StructuralScoreResult(
        score=score,
        confidence_band=band,
        z_scores={"mood": -0.2, "craving_inv": -0.1, "sleep_hours": 0.1, "self_efficacy": 0.2},
        baseline_n=8,
        recent_n=3,
        baseline_stats={
            key: {"mean": 5.0, "std": 1.0, "n": 8}
            for key in baseline.VARIABLES
        },
        recent_means={key: 4.8 for key in baseline.VARIABLES},
        composite_z=0.15,
    )


def _linguistic(
    *,
    eligible=True,
    ideation=False,
    indirect=False,
    crisis=False,
    rumination=0.7,
    negative_valence=0.6,
    signal_id=None,
):
    signal_id = (signal_id or uuid.uuid4()) if eligible else None
    raw = dict(VALID_ANALYSIS) if eligible else {}
    if eligible:
        raw.update(
            ideation_direct=ideation,
            ideation_indirect=indirect,
            consumption_crisis=crisis,
            rumination_score=rumination,
            negative_valence=negative_valence,
        )
    return {
        "_signal_uuid": signal_id,
        "signal_id": str(signal_id) if signal_id else None,
        "signal_timestamp": datetime.utcnow().isoformat() if signal_id else None,
        "eligible_for_risk": eligible,
        "freshness_window_hours": 12,
        "ideation_direct": ideation if eligible else False,
        "ideation_indirect": indirect if eligible else False,
        "consumption_crisis": crisis if eligible else False,
        "rumination_score": rumination if eligible else None,
        "negative_valence": raw.get("negative_valence") if eligible else None,
        "raw": raw,
    }


def _persistence(days, required):
    return {
        "band": "unstable",
        "window_days": required,
        "required_distinct_days": required,
        "observed_distinct_days": days,
        "observed_dates": [f"2026-08-{15 - i:02d}" for i in range(days)],
        "passed": days >= required,
    }


class LinguisticBoundaryTests(unittest.TestCase):
    def test_agent2_output_is_strict_and_bounded(self):
        self.assertEqual(LinguisticAnalysis.model_validate(VALID_ANALYSIS).rumination_score, 0.7)

        with self.assertRaises(ValidationError):
            LinguisticAnalysis.model_validate({**VALID_ANALYSIS, "alert_level": 4})
        with self.assertRaises(ValidationError):
            LinguisticAnalysis.model_validate({**VALID_ANALYSIS, "rumination_score": 1.1})
        with self.assertRaises(ValidationError):
            LinguisticAnalysis.model_validate({**VALID_ANALYSIS, "ideation_direct": "false"})

    def test_anthropic_analysis_preserves_non_clinical_metadata(self):
        usage = SimpleNamespace(
            input_tokens=17,
            output_tokens=23,
            cache_creation_input_tokens=2,
            cache_read_input_tokens=3,
        )
        response = SimpleNamespace(
            id="msg_test",
            model="claude-test-resolved",
            _request_id="req_test",
            stop_reason="end_turn",
            usage=usage,
            content=[SimpleNamespace(type="text", text='{"ok": true}')],
        )
        provider = AnthropicProvider()
        provider._client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_kwargs: response))

        result = provider.analyze_structured("static prompt", "synthetic input", {"input_schema": {"type": "object", "properties": {}}})

        self.assertEqual(result.value, {"ok": True})
        self.assertEqual(result.metadata.message_id, "msg_test")
        self.assertEqual(result.metadata.request_id, "req_test")
        self.assertEqual(result.metadata.response_model, "claude-test-resolved")
        self.assertEqual(result.metadata.input_tokens, 17)
        self.assertEqual(result.metadata.output_tokens, 23)
        self.assertIsNotNone(result.metadata.latency_ms)

    def test_agent1_configuration_failure_degrades_without_secondary_exception(self):
        patient_id = uuid.uuid4()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []

        def assign_primary_key(instance):
            if getattr(instance, "id", None) is None:
                instance.id = uuid.uuid4()

        db.refresh.side_effect = assign_primary_key
        provider = SimpleNamespace(chat=MagicMock(side_effect=RuntimeError("not configured")))
        assessment = SimpleNamespace(
            alert_level=0,
            assessment_reason="stable",
            input_signals={},
        )
        analysis = AnalysisOutcome(uuid.uuid4(), None, None, "configuration_error", None)

        with (
            patch.object(conversation, "analyze_text_and_store", return_value=analysis),
            patch.object(risk_engine, "run_and_persist", return_value=assessment),
            patch.object(conversation, "get_llm_provider", return_value=provider),
        ):
            reply = conversation.get_reply(
                db,
                SimpleNamespace(id=patient_id),
                "mensaje sintético",
            )

        self.assertEqual(reply["ui_mode"], "normal")
        self.assertIn("ANTHROPIC_API_KEY", reply["reply"])

    def test_risk_assessment_timestamp_serializes_with_explicit_utc_offset(self):
        output = RiskAssessmentOut(
            id=uuid.uuid4(),
            alert_level=0,
            triggering_rules=["N0_estable"],
            input_signals={},
            assessment_reason="stable",
            model_version="risk-engine-v1.2",
            calculated_at=datetime(2026, 8, 15, 12, 0, 0),
        ).model_dump(mode="json")

        self.assertTrue(output["calculated_at"].endswith("+00:00"))


class DeterministicExplanationTests(unittest.TestCase):
    def _calculate(
        self,
        *,
        structural=None,
        linguistic=None,
        n4=None,
        n3=None,
        persistence=(0, 0, 0),
        preferred_signal_id=None,
    ):
        # The production query returns newest-first and the engine reverses it
        # before calculating the slope.  The fake query does not implement SQL
        # ordering, so feed it in the same newest-first order here.
        checkins = [
            SimpleNamespace(id=uuid.uuid4(), sleep_hours=value, craving=4, created_at=datetime.utcnow())
            for value in (5.0, 6.0, 7.0)
        ]
        p1, p3, p5 = persistence
        persistence_values = {
            1: _persistence(p1, 1),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT: _persistence(p3, 3),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE: _persistence(p5, 5),
        }

        fake_db = _FakeDb(checkins)
        with (
            patch.object(baseline, "compute_structural_score", return_value=structural or _structural()),
            patch.object(
                risk_engine,
                "_linguistic_flags",
                return_value=linguistic or _linguistic(),
            ) as linguistic_lookup,
            patch.object(
                risk_engine,
                "_facts_in_categories",
                side_effect=[n4 or [], n3 or []],
            ),
            patch.object(
                risk_engine,
                "_persistence_detail",
                side_effect=lambda _db, _uid, _band, days: persistence_values[days],
            ),
        ):
            patient_id = uuid.uuid4()
            decision = risk_engine.calculate_risk_level(
                fake_db,
                patient_id,
                linguistic_signal_id=preferred_signal_id,
            )
        linguistic_lookup.assert_called_once_with(fake_db, patient_id, signal_id=preferred_signal_id)
        return decision

    def test_snapshot_has_all_rules_formulas_and_selected_rule(self):
        decision = self._calculate()

        self.assertEqual(decision.level, 0)
        trace = decision.calculation_trace
        self.assertEqual(trace["schema_version"], "risk-explanation-v1")
        # 11 original rules plus the five psychosocial-context ones.
        self.assertEqual(len(trace["rules"]), 16)
        self.assertEqual(sum(1 for rule in trace["rules"] if rule["selected"]), 1)
        self.assertEqual(trace["conclusion"]["selected_rule_code"], "N0_estable")
        self.assertEqual(trace["conclusion"]["matched_rule_codes"], ["N0_estable"])
        self.assertFalse(trace["rules"][-1]["matched"])
        self.assertIn("clamp(1 - composite_z / 3", trace["inputs"]["structural"]["composite"]["score_formula"])
        self.assertEqual(trace["inputs"]["sleep_trend"]["classification"], "empeorando")

    def test_priority_is_visible_when_multiple_rules_match(self):
        fact = {"id": str(uuid.uuid4()), "category": "planning", "created_at": datetime.utcnow().isoformat()}
        decision = self._calculate(
            structural=_structural(score=0.1, band="unstable"),
            linguistic=_linguistic(ideation=True, rumination=0.95),
            n4=[fact],
            persistence=(1, 3, 5),
        )

        self.assertEqual(decision.level, 4)
        self.assertEqual(decision.triggering_rules, ["N4_declaracion_ideacion_o_plan"])
        self.assertIn("N4_senal_linguistica_ideacion_directa", decision.calculation_trace["conclusion"]["matched_rule_codes"])
        selected = [rule for rule in decision.calculation_trace["rules"] if rule["selected"]]
        self.assertEqual(selected[0]["priority"], 1)

    def test_stale_agent2_signal_cannot_influence_risk(self):
        decision = self._calculate(
            structural=_structural(score=0.8, band="stable"),
            linguistic=_linguistic(eligible=False, ideation=True, crisis=True, rumination=0.99),
        )

        self.assertEqual(decision.level, 0)
        self.assertIsNone(decision.linguistic_signal_id)
        self.assertFalse(decision.calculation_trace["inputs"]["agent2"]["eligible_for_risk"])
        self.assertIsNone(decision.calculation_trace["inputs"]["agent2"]["values_used"])

    def test_chat_or_diary_evaluation_pins_its_exact_agent2_signal(self):
        crisis_signal_id = uuid.uuid4()
        benign_signal_id = uuid.uuid4()
        crisis = self._calculate(
            linguistic=_linguistic(ideation=True, signal_id=crisis_signal_id),
            preferred_signal_id=crisis_signal_id,
        )
        benign = self._calculate(
            linguistic=_linguistic(
                ideation=False,
                crisis=False,
                rumination=0.1,
                signal_id=benign_signal_id,
            ),
            preferred_signal_id=benign_signal_id,
        )

        self.assertEqual(crisis.level, 4)
        self.assertEqual(crisis.linguistic_signal_id, crisis_signal_id)
        self.assertEqual(benign.level, 0)
        self.assertEqual(benign.linguistic_signal_id, benign_signal_id)

    def test_sleep_trend_boundaries_are_strict(self):
        self.assertEqual(baseline.calculate_trend_detail([0.0, 0.15, 0.30]).label, "estable")
        self.assertEqual(baseline.calculate_trend_detail([0.0, 0.16, 0.32]).label, "aumentando")
        self.assertEqual(baseline.calculate_trend_detail([0.0, -0.16, -0.32]).label, "empeorando")
        self.assertEqual(baseline.calculate_trend_detail([1.0, 2.0]).label, "insuficiente")


class MigrationContractTests(unittest.TestCase):
    def test_migration_hardens_new_table_and_keeps_public_roles_out(self):
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "supabase"
            / "migrations"
            / "20260815120000_add_risk_explanations_agent2_tracking.sql"
        )
        if not migration_path.exists():
            self.skipTest("Repository-level migration is outside the backend-only Docker build context")
        migration = migration_path.read_text(encoding="utf-8")
        lowered = migration.lower()
        self.assertIn("force row level security", lowered)
        self.assertIn("to psychdeep_backend", lowered)
        self.assertIn("from public, anon, authenticated, service_role", lowered)
        self.assertIn("calculation_trace jsonb", lowered)
        self.assertIn("begin;", lowered)
        self.assertIn("set local role psychdeep_backend", lowered)
        self.assertIn("with set true", lowered)
        self.assertIn("revoke psychdeep_backend from postgres granted by postgres", lowered)
        self.assertIn("membership was not removed", lowered)


if __name__ == "__main__":
    unittest.main()
