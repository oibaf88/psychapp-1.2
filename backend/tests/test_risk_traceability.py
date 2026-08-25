import unittest
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.models import PatientProfile, PsychosocialObservation
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

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _limit):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def count(self):
        return len(self.rows)


class _FakeDb:
    """Fake session that dispatches by model.

    It used to return the check-in list for every model, which silently
    worked while the engine only read check-ins here. Now that the engine
    also folds in psychosocial observations, a catch-all would feed
    check-ins into that path, so the mapping is explicit.
    """

    def __init__(self, checkins=None, psychosocial=None, profile=None):
        self.checkins = checkins or []
        self.psychosocial = psychosocial or []
        # No profile by default, which is the case that must be preserved:
        # a patient the system has never met is evaluated on the absolute
        # constants, exactly as every patient was before profiles existed.
        self.profile = profile

    def query(self, model):
        if model is PsychosocialObservation:
            return _FakeQuery(self.psychosocial)
        if model is PatientProfile:
            return _FakeQuery([self.profile] if self.profile is not None else [])
        return _FakeQuery(self.checkins)


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
    ideation_indirect=False,
    crisis=False,
    rumination=0.7,
    negative_valence=0.4,
    signal_id=None,
):
    signal_id = (signal_id or uuid.uuid4()) if eligible else None
    raw = dict(VALID_ANALYSIS) if eligible else {}
    if eligible:
        raw.update(
            ideation_direct=ideation,
            ideation_indirect=ideation_indirect,
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
        "ideation_indirect": ideation_indirect if eligible else False,
        "consumption_crisis": crisis if eligible else False,
        "rumination_score": rumination if eligible else None,
        "negative_valence": negative_valence if eligible else None,
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

    def test_the_stored_turn_names_the_model_that_actually_answered(self):
        """Provenance comes from the call, not from re-reading the config.

        Re-resolving names the model the app would ask for now; the call's
        own metadata names the one the server said produced this text. On a
        local runtime those differ whenever the loaded weights are not the
        configured ones — which is the case the provenance exists for.
        """
        from app.services.llm.base import ChatResult, ProviderMetadata

        patient_id = uuid.uuid4()
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        db.refresh.side_effect = lambda i: None

        provider = SimpleNamespace(
            chat=MagicMock(
                return_value=ChatResult(
                    text="Te leo.",
                    metadata=ProviderMetadata(
                        provider="openai_compatible",
                        requested_model="llama-3.1-8b",
                        response_model="llama-3.1-70b-actually-loaded",
                        base_url="http://localhost:1234/v1",
                    ),
                )
            )
        )
        assessment = SimpleNamespace(alert_level=0, assessment_reason="stable", input_signals={})
        analysis = AnalysisOutcome(uuid.uuid4(), None, None, "succeeded", None)

        with (
            patch.object(conversation, "analyze_text_and_store", return_value=analysis),
            patch.object(risk_engine, "run_and_persist", return_value=assessment),
            patch.object(conversation, "get_llm_provider", return_value=provider),
        ):
            conversation.get_reply(db, SimpleNamespace(id=patient_id), "hola")

        stored = [
            call.args[0]
            for call in db.add.call_args_list
            if getattr(call.args[0], "role", None) == "assistant"
        ]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].model, "llama-3.1-70b-actually-loaded")
        self.assertEqual(stored[0].provider, "openai_compatible")
        self.assertEqual(stored[0].provider_base_url, "http://localhost:1234/v1")

    def test_a_template_only_reply_still_records_no_model(self):
        """A turn with no model behind it must keep saying so."""
        self.assertEqual(conversation._reply_provenance(MagicMock(), from_model=False), {})

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


class _CalculationHarness:
    """Shared fixture for driving calculate_risk_level with fakes."""

    def _calculate(
        self,
        *,
        structural=None,
        linguistic=None,
        n4=None,
        n3=None,
        persistence=(0, 0, 0),
        preferred_signal_id=None,
        psychosocial=None,
    ):
        # The production query returns newest-first and the engine reverses it
        # before calculating the slope.  The fake query does not implement SQL
        # ordering, so feed it in the same newest-first order here.
        checkins = [
            SimpleNamespace(id=uuid.uuid4(), sleep_hours=value, craving=5, created_at=datetime.utcnow())
            for value in (5.0, 6.0, 7.0)
        ]
        p1, p3, p5 = persistence
        persistence_values = {
            1: _persistence(p1, 1),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT: _persistence(p3, 3),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE: _persistence(p5, 5),
        }

        fake_db = _FakeDb(checkins, psychosocial=psychosocial or [])
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


class DeterministicExplanationTests(_CalculationHarness, unittest.TestCase):
    def test_snapshot_has_all_rules_formulas_and_selected_rule(self):
        decision = self._calculate()

        self.assertEqual(decision.level, 0)
        trace = decision.calculation_trace
        self.assertEqual(trace["schema_version"], "risk-explanation-v1")
        self.assertEqual(len(trace["rules"]), 17)
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


def _psychosocial_row(
    *,
    domain="housing",
    category="housing_temporary",
    valence="risk",
    intensity=1.0,
    confidence=1.0,
    is_change=False,
    status="inferred",
    days_ago=1,
):
    from datetime import timedelta

    return SimpleNamespace(
        id=uuid.uuid4(),
        domain=domain,
        category=category,
        valence=valence,
        intensity=intensity,
        confidence=confidence,
        is_change=is_change,
        status=status,
        summary="resumen",
        evidence_quote="cita",
        observed_at=datetime.utcnow() - timedelta(days=days_ago),
    )


class PsychosocialRuleTests(_CalculationHarness, unittest.TestCase):
    """The social-context rules added in v2.

    The safety property under test throughout: psychosocial data can DEEPEN
    an assessment, but on its own it can never reach level 3 and therefore
    can never page a clinician.
    """

    def test_psychosocial_index_alone_never_exceeds_level_two(self):
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            psychosocial=[
                _psychosocial_row(domain="housing", category="housing_homeless"),
                _psychosocial_row(domain="social_support", category="support_absent"),
                _psychosocial_row(domain="economic", category="food_insecurity"),
            ],
        )
        self.assertEqual(decision.level, 2)
        self.assertEqual(decision.triggering_rules, ["N2_vulnerabilidad_psicosocial"])

    def test_acute_change_plus_worsening_sleep_reaches_level_three(self):
        """The whole point of v2: a small social sentence plus one other
        signal is what precedes a crisis."""
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            psychosocial=[
                _psychosocial_row(category="housing_temporary", is_change=True, days_ago=1),
            ],
        )
        # The fake check-ins encode a worsening sleep slope.
        self.assertEqual(decision.calculation_trace["inputs"]["sleep_trend"]["classification"], "empeorando")
        self.assertEqual(decision.level, 3)
        self.assertEqual(decision.triggering_rules, ["N3_desestabilizacion_psicosocial_aguda"])

    def test_acute_change_without_any_corroboration_stays_below_level_three(self):
        stable_sleep = [
            SimpleNamespace(id=uuid.uuid4(), sleep_hours=7.0, craving=5, created_at=datetime.utcnow())
            for _ in range(3)
        ]
        fake_db = _FakeDb(
            stable_sleep,
            psychosocial=[_psychosocial_row(category="housing_temporary", is_change=True, days_ago=1)],
        )
        persistence_values = {
            1: _persistence(0, 1),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT: _persistence(0, 3),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE: _persistence(0, 5),
        }
        with (
            patch.object(baseline, "compute_structural_score", return_value=_structural(score=0.9, band="stable")),
            patch.object(risk_engine, "_linguistic_flags", return_value=_linguistic(rumination=0.1)),
            patch.object(risk_engine, "_facts_in_categories", side_effect=[[], []]),
            patch.object(
                risk_engine,
                "_persistence_detail",
                side_effect=lambda _db, _uid, _band, days: persistence_values[days],
            ),
        ):
            decision = risk_engine.calculate_risk_level(fake_db, uuid.uuid4())

        self.assertLess(decision.level, 3)
        self.assertNotIn("N3_desestabilizacion_psicosocial_aguda", decision.triggering_rules)

    def test_high_index_with_unstable_band_reaches_level_three(self):
        decision = self._calculate(
            structural=_structural(score=0.3, band="unstable"),
            linguistic=_linguistic(rumination=0.1),
            psychosocial=[
                _psychosocial_row(domain="housing", category="housing_homeless"),
                _psychosocial_row(domain="social_support", category="support_absent"),
            ],
        )
        self.assertEqual(decision.level, 3)
        self.assertIn(
            decision.triggering_rules[0],
            ("N3_desestabilizacion_psicosocial_aguda", "N3_convergencia_psicosocial_estructural"),
        )

    def test_refuted_observations_cannot_raise_the_level(self):
        """A therapist refuting an extraction must actually de-escalate."""
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            psychosocial=[
                _psychosocial_row(category="housing_temporary", is_change=True, status="refuted"),
                _psychosocial_row(
                    domain="social_support", category="support_absent", status="refuted"
                ),
            ],
        )
        self.assertEqual(decision.level, 0)
        self.assertIsNone(decision.calculation_trace["derivations"]["psychosocial_index"])

    def test_existing_higher_priority_rules_still_win(self):
        fact = {"id": str(uuid.uuid4()), "category": "planning", "created_at": datetime.utcnow().isoformat()}
        decision = self._calculate(
            n4=[fact],
            psychosocial=[_psychosocial_row(category="housing_homeless")],
        )
        self.assertEqual(decision.level, 4)
        self.assertEqual(decision.triggering_rules, ["N4_declaracion_ideacion_o_plan"])

    def test_trace_records_the_index_and_its_domain_breakdown(self):
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            psychosocial=[_psychosocial_row(domain="economic", category="benefit_loss")],
        )
        block = decision.calculation_trace["inputs"]["psychosocial"]
        self.assertIsNotNone(block["index"])
        self.assertEqual(len(block["domains"]), 1)
        self.assertEqual(block["domains"][0]["domain"], "economic")
        self.assertIn("clamp", block["formula"])
        self.assertIn("psychosocial", decision.input_signals)

    def test_no_psychosocial_data_leaves_previous_behaviour_untouched(self):
        decision = self._calculate(structural=_structural(score=0.9, band="stable"))
        self.assertEqual(decision.level, 0)
        self.assertEqual(decision.triggering_rules, ["N0_estable"])


class InterpersonalConvergenceRuleTests(unittest.TestCase, _CalculationHarness):
    """The constellation that reads as harmless message by message.

    Each leg of the level-4 rule is, on its own, something a person might say
    on an ordinary bad week. The rule exists because their coincidence is not
    ordinary, and because nothing else in the pipeline was able to see it: the
    linguistic flags stay false and the structural score never moves.
    """

    def _rising_craving_checkins(self):
        return [
            SimpleNamespace(id=uuid.uuid4(), sleep_hours=7.0, craving=value, created_at=datetime.utcnow())
            for value in (9, 6, 2)  # newest first; the engine reverses it
        ]

    def _interpersonal_context(self, *, leave_taking=True, days_ago=2):
        rows = [
            _psychosocial_row(
                domain="perceived_burden",
                category="burden_expressed",
                intensity=0.9,
                days_ago=days_ago,
            ),
            _psychosocial_row(
                domain="thwarted_belonging",
                category="belonging_absent",
                intensity=0.9,
                days_ago=days_ago,
            ),
        ]
        if leave_taking:
            rows.append(
                _psychosocial_row(
                    domain="leave_taking",
                    category="giving_possessions_away",
                    intensity=0.8,
                    is_change=True,
                    days_ago=days_ago,
                )
            )
        return rows

    def test_the_whole_constellation_reaches_level_four(self):
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            linguistic=_linguistic(ideation_indirect=True, rumination=0.2),
            psychosocial=self._interpersonal_context(),
        )
        self.assertEqual(decision.level, 4)
        self.assertEqual(decision.triggering_rules, ["N4_convergencia_interpersonal_despedida"])

    def test_without_the_leave_taking_signal_it_does_not_reach_level_four(self):
        """Removing one leg must de-escalate: the rule is a conjunction."""
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            linguistic=_linguistic(ideation_indirect=True, rumination=0.2),
            psychosocial=self._interpersonal_context(leave_taking=False),
        )
        self.assertLess(decision.level, 4)
        self.assertNotIn("N4_convergencia_interpersonal_despedida", decision.triggering_rules)

    def test_without_indirect_ideation_it_does_not_reach_level_four(self):
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            linguistic=_linguistic(ideation_indirect=False, rumination=0.2),
            psychosocial=self._interpersonal_context(),
        )
        self.assertLess(decision.level, 4)
        self.assertNotIn("N4_convergencia_interpersonal_despedida", decision.triggering_rules)

    def test_chronic_interpersonal_risk_alone_does_not_keep_re_alerting(self):
        """Expressed months ago, it is context; expressed this week, a signal.

        Without this window a chronic "nobody needs me" would re-raise the
        same alarm every single day the therapist closed it.
        """
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            linguistic=_linguistic(ideation_indirect=True, rumination=0.2),
            psychosocial=self._interpersonal_context(days_ago=90),
        )
        self.assertLess(decision.level, 3)

    def test_live_interpersonal_risk_alone_reaches_level_three(self):
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            linguistic=_linguistic(rumination=0.1),
            psychosocial=self._interpersonal_context(leave_taking=False),
        )
        self.assertEqual(decision.level, 3)
        self.assertEqual(decision.triggering_rules, ["N3_riesgo_interpersonal_alto"])

    def test_low_confidence_readings_never_move_a_threshold(self):
        """A hedged or ironic mention is shown to the therapist, not scored."""
        rows = [
            _psychosocial_row(
                domain="perceived_burden", category="burden_expressed", intensity=1.0, confidence=0.3
            ),
            _psychosocial_row(
                domain="thwarted_belonging", category="belonging_absent", intensity=1.0, confidence=0.3
            ),
        ]
        decision = self._calculate(
            structural=_structural(score=0.9, band="stable"),
            linguistic=_linguistic(ideation_indirect=True, rumination=0.2),
            psychosocial=rows,
        )
        self.assertLess(decision.level, 3)
        block = decision.calculation_trace["inputs"]["psychosocial"]
        self.assertIsNone(block["indices"]["interpersonal_risk_index"])
        self.assertEqual(block["scored_count"], 0)
        self.assertEqual(len(block["domains"]), 2)

    def test_relapse_context_with_rising_craving_reaches_level_three(self):
        fake_db = _FakeDb(
            self._rising_craving_checkins(),
            psychosocial=[
                _psychosocial_row(
                    domain="substance_environment",
                    category="using_environment_exposure",
                    intensity=0.9,
                ),
                _psychosocial_row(
                    domain="cohabitation", category="lives_with_people_who_use", intensity=0.9
                ),
            ],
        )
        persistence_values = {
            1: _persistence(0, 1),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT: _persistence(0, 3),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE: _persistence(0, 5),
        }
        with (
            patch.object(baseline, "compute_structural_score", return_value=_structural(score=0.9, band="stable")),
            patch.object(risk_engine, "_linguistic_flags", return_value=_linguistic(rumination=0.1)),
            patch.object(risk_engine, "_facts_in_categories", side_effect=[[], []]),
            patch.object(
                risk_engine,
                "_persistence_detail",
                side_effect=lambda _db, _uid, _band, days: persistence_values[days],
            ),
        ):
            decision = risk_engine.calculate_risk_level(fake_db, uuid.uuid4())

        self.assertEqual(decision.level, 3)
        self.assertEqual(decision.triggering_rules, ["N3_riesgo_recaida_contextual"])

    def test_rules_reading_absent_data_are_recorded_as_not_evaluable(self):
        """Absence of data is not evidence of safety, and says so in the trace."""
        decision = self._calculate(structural=_structural(score=0.9, band="stable"))
        by_code = {rule["code"]: rule for rule in decision.calculation_trace["rules"]}
        for code in (
            "N4_convergencia_interpersonal_despedida",
            "N3_riesgo_interpersonal_alto",
            "N3_riesgo_recaida_contextual",
            "N2_vulnerabilidad_psicosocial",
        ):
            self.assertEqual(by_code[code]["status"], "not_evaluable", code)
            self.assertIsNone(by_code[code]["matched"], code)

    def test_declared_ideation_still_outranks_the_new_rule(self):
        fact = {"id": str(uuid.uuid4()), "category": "planning", "created_at": datetime.utcnow().isoformat()}
        decision = self._calculate(
            n4=[fact],
            linguistic=_linguistic(ideation_indirect=True),
            psychosocial=self._interpersonal_context(),
        )
        self.assertEqual(decision.triggering_rules, ["N4_declaracion_ideacion_o_plan"])
