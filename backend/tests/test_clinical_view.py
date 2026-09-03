"""Tests for the therapist-facing explanation layer.

The point of these is the property the previous UI got wrong: a high
structural score sitting next to a level-4 alert must be explained, not
just displayed, and every explanation must name the evidence family that
actually drove the level.
"""
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.services import clinical_view


def _assessment(
    *,
    level,
    rule,
    score=0.91,
    band="stable",
    z_scores=None,
    variables=None,
    facts=None,
):
    z_scores = z_scores if z_scores is not None else {
        "mood": -0.1,
        "craving_inv": 0.2,
        "sleep_hours": 0.05,
        "self_efficacy": -0.15,
    }
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        alert_level=level,
        triggering_rules=[rule],
        assessment_reason="motivo",
        calculated_at=datetime(2026, 8, 14, 10, 0, 0),
        generated_alert_id=None,
        agent2_trace_id=None,
        linguistic_signal_id_used=None,
        input_signals={
            "structural_score": score,
            "confidence_band": band,
            "z_scores": z_scores,
            "sleep_trend": "estable",
            "sleep_trend_slope": 0.02,
        },
        input_facts=facts or {"n4_declarations": [], "n3_declarations": []},
        calculation_trace={
            "conclusion": {"selected_rule_code": rule},
            "inputs": {
                "structural": {
                    "baseline_sample_count": 9,
                    "recent_sample_count": 5,
                    "variables": variables
                    or [
                        {
                            "key": key,
                            "baseline_mean": 5.0,
                            "baseline_population_std": 1.0,
                            "recent_mean": 5.0 + z_scores[key],
                            "difference": z_scores[key],
                            "z_score": z_scores[key],
                        }
                        for key in ("mood", "craving_inv", "sleep_hours", "self_efficacy")
                    ],
                    "composite": {"composite_z": 0.27, "score": score, "band": band},
                }
            },
        },
    )


class LevelExplanationTests(unittest.TestCase):
    def test_linguistic_level_four_is_attributed_to_the_text_not_the_score(self):
        explanation = clinical_view.level_explanation(
            _assessment(level=4, rule="N4_senal_linguistica_ideacion_directa")
        )
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_LINGUISTIC)
        self.assertIn("ESCRIBIÓ", explanation["headline"])
        self.assertEqual(explanation["level_label"], "Nivel 4 · Emergencia")

    def test_high_score_with_level_four_gets_an_explicit_reconciliation(self):
        """0.91/stable + level 4 is the exact pair that read as a bug."""
        explanation = clinical_view.level_explanation(
            _assessment(level=4, rule="N4_senal_linguistica_ideacion_directa", score=0.91, band="stable")
        )
        text = explanation["structural_reconciliation"]
        self.assertIsNotNone(text)
        self.assertIn("0.91", text)
        self.assertIn("No hay contradicción", text)

    def test_confirmed_fact_level_is_marked_as_fact_not_inference(self):
        explanation = clinical_view.level_explanation(
            _assessment(level=4, rule="N4_declaracion_ideacion_o_plan")
        )
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_CONFIRMED_FACT)
        self.assertIn("HECHO", explanation["headline"])

    def test_historical_structural_rule_keeps_its_original_attribution(self):
        explanation = clinical_view.level_explanation(
            _assessment(level=3, rule="N3_unstable_persistente", score=0.21, band="unstable")
        )
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_STRUCTURAL)
        self.assertIn("histórica", explanation["structural_reconciliation"])
        self.assertIn("0.21", explanation["structural_reconciliation"])

    def test_v2_structural_rule_explains_separate_adverse_component(self):
        assessment = _assessment(level=3, rule="N3_unstable_persistente", score=0.21, band="unstable")
        assessment.input_signals.update(
            structural_calculation_version="structural-v2", deterioration_band="unstable",
        )
        explanation = clinical_view.level_explanation(assessment)
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_STRUCTURAL)
        self.assertIn("por separado", explanation["structural_reconciliation"])
        self.assertIn("unstable", explanation["structural_reconciliation"])
        self.assertNotIn("histórica", explanation["structural_reconciliation"])

    def test_no_assessment_does_not_crash(self):
        explanation = clinical_view.level_explanation(None)
        self.assertIsNone(explanation["level"])
        self.assertEqual(explanation["level_label"], "Sin evaluación")

    def test_unknown_rule_code_degrades_gracefully(self):
        explanation = clinical_view.level_explanation(_assessment(level=2, rule="N9_regla_del_futuro"))
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_NONE)
        self.assertEqual(explanation["rule_title"], "N9_regla_del_futuro")

    def test_selected_rule_falls_back_to_the_persisted_trace(self):
        assessment = _assessment(level=3, rule="N3_unstable_persistente")
        assessment.triggering_rules = []
        self.assertEqual(clinical_view.selected_rule_code(assessment), "N3_unstable_persistente")


class StructuralExplanationTests(unittest.TestCase):
    def _v2_assessment(self, z_scores=None):
        assessment = _assessment(
            level=0, rule="N0_estable", score=0.4, band="transition",
            z_scores=z_scores or {"mood": 2.0, "craving_inv": 2.0, "sleep_hours": 0.0, "self_efficacy": 2.0},
        )
        structural = assessment.calculation_trace["inputs"]["structural"]
        structural["calculation_version"] = "structural-v2"
        structural["composite"].update(deterioration_score=1.0, deterioration_band="stable")
        return assessment

    def test_direction_is_reported_per_variable(self):
        explanation = clinical_view.structural_explanation(
            _assessment(
                level=3,
                rule="N3_unstable_persistente",
                score=0.30,
                band="unstable",
                z_scores={
                    "mood": -1.8,
                    "craving_inv": -1.2,
                    "sleep_hours": -0.2,
                    "self_efficacy": 0.1,
                },
            )
        )
        by_key = {row["key"]: row for row in explanation["variables"]}
        self.assertEqual(by_key["mood"]["direction"], "peor")
        self.assertEqual(by_key["self_efficacy"]["direction"], "igual")
        self.assertIn("ADVERSA", explanation["direction_summary"])
        self.assertIn("Ánimo", explanation["direction_summary"])

    def test_a_large_improvement_also_lowers_the_score_and_is_flagged(self):
        """The composite is a mean of |z|, so improvement depresses it too.

        Without this reading a therapist would treat a recovering patient as
        deteriorating.
        """
        explanation = clinical_view.structural_explanation(
            _assessment(
                level=2,
                rule="N2_desviacion_moderada",
                score=0.40,
                band="transition",
                z_scores={
                    "mood": 2.0,
                    "craving_inv": 1.6,
                    "sleep_hours": 1.2,
                    "self_efficacy": 1.4,
                },
            )
        )
        self.assertIn("FAVORABLE", explanation["direction_summary"])
        self.assertEqual(explanation["adverse_composite_z"], 0.0)
        self.assertGreater(explanation["favourable_composite_z"], 1.0)

    def test_scale_note_states_that_high_means_stable_not_safe(self):
        explanation = clinical_view.structural_explanation(
            _assessment(level=0, rule="N0_estable", score=0.95, band="stable")
        )
        self.assertIn("SIMILITUD", explanation["scale_note"])
        self.assertIn("nunca «sin riesgo»", explanation["scale_note"])
        self.assertIn("estable", explanation["summary"])

    def test_thin_recent_window_adds_a_caveat(self):
        assessment = _assessment(level=0, rule="N0_estable")
        assessment.calculation_trace["inputs"]["structural"]["recent_sample_count"] = 1
        explanation = clinical_view.structural_explanation(assessment)
        self.assertTrue(any("muy sensible" in caveat for caveat in explanation["caveats"]))

    def test_missing_score_returns_the_empty_shape(self):
        assessment = _assessment(level=1, rule="N1_datos_insuficientes_o_sin_criterios")
        assessment.input_signals = {"structural_score": None, "confidence_band": None}
        explanation = clinical_view.structural_explanation(assessment)
        self.assertIsNone(explanation["score"])
        self.assertEqual(explanation["variables"], [])

    def test_v2_explains_new_formula_and_separates_favourable_change_from_deterioration(self):
        explanation = clinical_view.structural_explanation(self._v2_assessment())
        self.assertEqual(explanation["calculation_version"], "structural-v2")
        self.assertEqual(explanation["score"], 0.4)
        self.assertEqual(explanation["deterioration_score"], 1.0)
        self.assertEqual(explanation["deterioration_band"], "stable")
        self.assertEqual(explanation["adverse_composite_z"], 0.0)
        self.assertEqual(explanation["favourable_composite_z"], 1.5)
        self.assertIn("FAVORABLE", explanation["direction_summary"])
        self.assertIn("1.20", explanation["band_meaning"])
        self.assertNotIn("0.60", explanation["band_meaning"])
        self.assertTrue(any("1 / (1 + media de |z|)" in text for text in explanation["caveats"]))
        self.assertTrue(any("no una escala clínica validada" in text for text in explanation["caveats"]))

    def test_v2_sleep_increase_is_bilateral_not_favourable(self):
        assessment = self._v2_assessment({"mood": 0.0, "craving_inv": 0.0, "sleep_hours": 3.0, "self_efficacy": 0.0})
        explanation = clinical_view.structural_explanation(assessment)
        by_key = {row["key"]: row for row in explanation["variables"]}
        self.assertEqual(by_key["sleep_hours"]["direction"], "cambio")
        self.assertIn("no implica por sí solo mejoría", by_key["sleep_hours"]["reading"])
        self.assertEqual(explanation["adverse_composite_z"], 0.75)
        self.assertEqual(explanation["favourable_composite_z"], 0.0)
        self.assertIn("sueño", explanation["direction_summary"])
        self.assertNotIn("FAVORABLE", explanation["direction_summary"])

    def test_v2_prefers_persisted_unrounded_input_aggregates(self):
        assessment = self._v2_assessment()
        assessment.calculation_trace["inputs"]["structural"]["composite"].update(
            adverse_composite_z=0.001, favourable_composite_z=1.499,
        )
        explanation = clinical_view.structural_explanation(assessment)
        self.assertEqual(explanation["adverse_composite_z"], 0.001)
        self.assertEqual(explanation["favourable_composite_z"], 1.499)

    def test_v2_partial_axes_do_not_become_zero_or_shorter_denominator(self):
        assessment = self._v2_assessment()
        assessment.input_signals["z_scores"].pop("mood")
        structural = assessment.calculation_trace["inputs"]["structural"]
        structural["variables"] = [row for row in structural["variables"] if row["key"] != "mood"]
        explanation = clinical_view.structural_explanation(assessment)
        self.assertIsNone(explanation["adverse_composite_z"])
        self.assertIsNone(explanation["favourable_composite_z"])
        self.assertIn("Faltan datos", explanation["direction_summary"])

    def test_v2_stale_baseline_is_disclosed(self):
        assessment = self._v2_assessment()
        assessment.calculation_trace["inputs"]["structural"]["baseline_is_stale"] = True
        explanation = clinical_view.structural_explanation(assessment)
        self.assertTrue(explanation["baseline_is_stale"])
        self.assertTrue(any("referencia antigua" in text for text in explanation["caveats"]))

    def test_historical_zero_is_preserved_with_old_formula_not_relabelled_v2(self):
        assessment = _assessment(level=2, rule="N2_desviacion_moderada", score=0.0, band="unstable")
        explanation = clinical_view.structural_explanation(assessment)
        self.assertEqual(explanation["score"], 0.0)
        self.assertEqual(explanation["calculation_version"], "structural-v1")
        self.assertIsNone(explanation["deterioration_score"])
        self.assertIsNone(explanation["deterioration_band"])
        self.assertTrue(any("conserva el cálculo guardado" in text for text in explanation["caveats"]))
        self.assertTrue(any("max(0, 1 − media de |z| / 3)" in text for text in explanation["caveats"]))


class ExcerptTests(unittest.TestCase):
    def test_excerpt_is_truncated_with_an_ellipsis(self):
        text = "a" * 500
        excerpt = clinical_view._excerpt(text, 50)
        self.assertEqual(len(excerpt), 50)
        self.assertTrue(excerpt.endswith("…"))

    def test_short_text_is_returned_untouched(self):
        self.assertEqual(clinical_view._excerpt("  hola  ", 50), "hola")

    def test_none_text_is_empty(self):
        self.assertEqual(clinical_view._excerpt(None), "")


class InterpersonalEvidenceTests(unittest.TestCase):
    def test_recent_indirect_source_remains_driver_after_a_neutral_message(self):
        from app.services import risk_engine
        from test_risk_traceability import InterpersonalConvergenceRuleTests, _linguistic, _structural

        harness = InterpersonalConvergenceRuleTests()
        prior_signal_id, current_signal_id = uuid.uuid4(), uuid.uuid4()
        safety = {
            "ideation_indirect": True,
            "ideation_direct": False,
            "consumption_crisis": False,
            "window_hours": 12,
            "evidence": [{"signal_id": str(prior_signal_id), "ideation_indirect": True}],
        }
        with patch.object(risk_engine, "_recent_safety_signals", return_value=safety):
            decision = harness._calculate(
                structural=_structural(score=0.9, band="stable"),
                linguistic=_linguistic(signal_id=current_signal_id, ideation_indirect=False, rumination=0.2),
                psychosocial=harness._interpersonal_context(),
                preferred_signal_id=current_signal_id,
            )
        self.assertEqual(decision.triggering_rules, ["N4_convergencia_interpersonal_despedida"])
        self.assertEqual(decision.input_signals["safety_driver_signal_id"], str(prior_signal_id))
        self.assertEqual(decision.linguistic_signal_id, current_signal_id)

    def _evidence_fixture(self):
        assessment = _assessment(level=4, rule="N4_convergencia_interpersonal_despedida")
        signal_id, trace_id, message_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        assessment.linguistic_signal_id_used = uuid.uuid4()
        assessment.input_signals["safety_driver_signal_id"] = str(signal_id)
        signal = SimpleNamespace(
            id=signal_id, user_id=assessment.user_id, agent2_trace_id=trace_id,
            value={"ideation_indirect": True},
        )
        trace = SimpleNamespace(
            id=trace_id, user_id=assessment.user_id, source_type="chat_message",
            chat_message_id=message_id, diary_entry_id=None,
        )
        message = SimpleNamespace(
            id=message_id, user_id=assessment.user_id, content="Texto sintético que originó la señal.",
            created_at=assessment.calculated_at,
        )
        records = {
            (clinical_view.AlfaSignal, signal_id): signal,
            (clinical_view.Agent2AnalysisTrace, trace_id): trace,
            (clinical_view.ChatMessage, message_id): message,
        }
        db = SimpleNamespace(get=lambda model, key: records.get((model, key)))
        return assessment, db, signal, trace, message

    def test_convergence_evidence_resolves_the_actual_textual_driver(self):
        assessment, db, signal, trace, message = self._evidence_fixture()
        explanation = clinical_view.level_explanation(assessment)
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_CONVERGENCE)
        self.assertNotIn("no se cumplió", explanation["headline"])
        evidence = clinical_view.evidence_for_assessment(db, assessment)
        self.assertEqual(evidence["kind"], "texto")
        self.assertEqual(evidence["signal_id"], str(signal.id))
        self.assertEqual(evidence["trace_id"], str(trace.id))
        self.assertEqual(evidence["source_id"], str(message.id))
        self.assertEqual(evidence["text"], message.content)

    def test_convergence_evidence_does_not_disclose_another_patients_text(self):
        for foreign_row in ("signal", "trace", "message"):
            with self.subTest(foreign_row=foreign_row):
                assessment, db, signal, trace, message = self._evidence_fixture()
                {"signal": signal, "trace": trace, "message": message}[foreign_row].user_id = uuid.uuid4()
                evidence = clinical_view.evidence_for_assessment(db, assessment)
                self.assertFalse(evidence and evidence.get("text"))


class RuleCatalogTests(unittest.TestCase):
    def test_every_engine_rule_has_a_therapist_facing_entry(self):
        """A rule the engine can select but the catalog does not know would
        render as an untranslated code in the panel."""
        from app.services import risk_engine

        engine_codes = {
            "N4_declaracion_ideacion_o_plan",
            "N4_senal_linguistica_ideacion_directa",
            "N4_convergencia_interpersonal_despedida",
            "N4_convergencia_critica_extrema",
            "N3_declaracion_crisis_consumo",
            "N3_declaracion_recaida",
            "N3_senal_linguistica_crisis_consumo",
            "N3_unstable_persistente_con_convergencia",
            "N3_unstable_persistente",
            "N2_desviacion_moderada",
            "N0_estable",
            "N1_datos_insuficientes_o_sin_criterios",
            "N1_sin_criterios_superiores",
        }
        self.assertEqual(engine_codes - set(clinical_view.RULE_CATALOG), set())
        # Guard against the engine growing a rule without a catalog entry.
        self.assertTrue(hasattr(risk_engine, "calculate_risk_level"))

    def test_catalog_levels_match_their_rule_prefix(self):
        for code, info in clinical_view.RULE_CATALOG.items():
            self.assertEqual(int(code[1]), info["level"], code)




class EvidenceBatchingTests(unittest.TestCase):
    def test_evidence_for_assessments_matches_individual_calls(self):
        from app.database import Base
        from app.models import User, ChatMessage, Agent2AnalysisTrace, AlfaSignal, RiskAssessment
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from datetime import timezone

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        db = Session()

        patient = User(id=uuid.uuid4(), email="p@example.com", display_name="P1", role="patient", hashed_password="x")
        db.add(patient)
        db.commit()

        now = datetime.now(timezone.utc)
        assessments = []
        for i in range(5):
            msg = ChatMessage(id=uuid.uuid4(), user_id=patient.id, role="user", content=f"Test message {i}", created_at=now)
            db.add(msg)
            db.commit()

            trace = Agent2AnalysisTrace(
                id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
                user_id=patient.id,
                agent_role="analyzer_merged",
                source_type="chat_message",
                chat_message_id=msg.id,
                status="succeeded",
                requested_model="m",
                response_model="m",
                effort="none",
                max_tokens=100,
                prompt_version="v1",
                prompt_sha256="p",
                schema_version="v1",
                schema_sha256="s",
                started_at=now,
                created_at=now
            )
            db.add(trace)
            db.commit()

            signal = AlfaSignal(
                id=uuid.uuid4(),
                user_id=patient.id,
                agent2_trace_id=trace.id,
                signal_type="linguistic",
                value={"short_rationale": "batch test"},
                timestamp=now
            )
            db.add(signal)
            db.commit()

            assessment = RiskAssessment(
                id=uuid.uuid4(),
                user_id=patient.id,
                agent2_trace_id=trace.id,
                linguistic_signal_id_used=signal.id,
                triggering_rules=["N2_marcadore_ling_prioritarios"],
                input_signals={},
                assessment_reason="test",
                alert_level=2,
                calculated_at=now
            )
            db.add(assessment)
            db.commit()
            assessments.append(assessment)

        individual_results = {a.id: clinical_view.evidence_for_assessment(db, a) for a in assessments}
        batch_results = clinical_view.evidence_for_assessments(db, assessments)

        self.assertEqual(batch_results, individual_results)


if __name__ == "__main__":
    unittest.main()
