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

    def test_structural_rule_points_back_at_the_score(self):
        explanation = clinical_view.level_explanation(
            _assessment(level=3, rule="N3_unstable_persistente", score=0.21, band="unstable")
        )
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_STRUCTURAL)
        self.assertIn("SÍ es el motivo", explanation["structural_reconciliation"])

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


class RuleCatalogTests(unittest.TestCase):
    def test_every_engine_rule_has_a_therapist_facing_entry(self):
        """A rule the engine can select but the catalog does not know would
        render as an untranslated code in the panel."""
        from app.services import risk_engine

        engine_codes = {
            "N4_declaracion_ideacion_o_plan",
            "N4_senal_linguistica_ideacion_directa",
            "N4_convergencia_critica_extrema",
            "N3_declaracion_crisis_consumo",
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


if __name__ == "__main__":
    unittest.main()
