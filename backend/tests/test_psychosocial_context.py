"""Tests for the psychosocial layer: Agent 4, its index and its risk rules.

The properties that matter here are the ones the feature exists for:

  * a message that trips no linguistic flag and touches no check-in can still
    move the alert level, when the social context inside it says so;
  * none of that happens for a patient with no psychosocial data, so the
    previous behaviour is untouched;
  * a professional can disagree with any single reading, and their word wins
    over the model's afterwards;
  * everything the engine used is stored, in Spanish, with the sentence it
    came from.
"""
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.content.psychosocial_catalog import DOMAIN_BY_KEY, DOMAIN_KEYS
from app.models import CheckIn, PsychosocialObservation
from app.services import baseline, clinical_view, risk_engine
from app.services import psychosocial as psychosocial_service
from app.services.psychosocial import PsychosocialExtraction, build_profile

VALID_EXTRACTION = {
    "has_psychosocial_content": True,
    "overall_note": "Habla de la pérdida de la vivienda y de quedarse sin apoyos.",
    "observations": [
        {
            "domain": "vivienda",
            "state": "riesgo_alto",
            "direction": "empeora",
            "onset": "reciente",
            "confidence": 0.9,
            "summary": "El casero le ha dado un mes para dejar el piso.",
            "evidence_quote": "me han dado un mes para dejar el piso",
        }
    ],
}


def _obs(
    domain,
    state,
    *,
    direction="estable",
    onset="desconocido",
    days_ago=1,
    confidence=0.9,
    recorded_by="agent4",
    confirmed=False,
    summary=None,
    quote="cita literal del paciente",
):
    observed = datetime.utcnow() - timedelta(days=days_ago)
    return SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        domain=domain,
        state=state,
        direction=direction,
        onset=onset,
        confidence=confidence,
        summary=summary or f"Situación de {domain}",
        evidence_quote=quote,
        source_type="chat_message",
        source_id=uuid.uuid4(),
        recorded_by=recorded_by,
        confirmed_fact_id=uuid.uuid4() if confirmed else None,
        is_current=True,
        dismissed_at=None,
        dismissed_reason=None,
        observed_at=observed,
        created_at=observed,
    )


class Agent4BoundaryTests(unittest.TestCase):
    def test_output_is_strict_and_bounded(self):
        parsed = PsychosocialExtraction.model_validate(VALID_EXTRACTION)
        self.assertEqual(parsed.observations[0].domain, "vivienda")

        with self.assertRaises(ValidationError):
            PsychosocialExtraction.model_validate({**VALID_EXTRACTION, "alert_level": 4})
        with self.assertRaises(ValidationError):
            PsychosocialExtraction.model_validate(
                {
                    **VALID_EXTRACTION,
                    "observations": [{**VALID_EXTRACTION["observations"][0], "confidence": 1.4}],
                }
            )
        with self.assertRaises(ValidationError):
            PsychosocialExtraction.model_validate(
                {
                    **VALID_EXTRACTION,
                    "observations": [{**VALID_EXTRACTION["observations"][0], "domain": "astrologia"}],
                }
            )
        with self.assertRaises(ValidationError):
            PsychosocialExtraction.model_validate(
                {
                    **VALID_EXTRACTION,
                    "observations": [{**VALID_EXTRACTION["observations"][0], "state": "fatal"}],
                }
            )

    def test_prompt_and_schema_cover_every_catalogued_domain(self):
        from app.content.prompts import AGENT4_SYSTEM_PROMPT, AGENT4_TOOL_SCHEMA

        enum = AGENT4_TOOL_SCHEMA["input_schema"]["properties"]["observations"]["items"]["properties"][
            "domain"
        ]["enum"]
        self.assertEqual(sorted(enum), sorted(DOMAIN_KEYS))
        for key in DOMAIN_KEYS:
            self.assertIn(f"`{key}`", AGENT4_SYSTEM_PROMPT)

    def test_short_text_never_reaches_the_provider(self):
        db = MagicMock()
        with patch.object(psychosocial_service, "get_llm_provider") as provider:
            outcome = psychosocial_service.extract_and_store(
                db,
                uuid.uuid4(),
                "ok",
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        provider.assert_not_called()
        self.assertEqual(outcome.status, "skipped_short_text")

    def test_provider_failure_degrades_without_raising(self):
        db = MagicMock()
        trace = SimpleNamespace(id=uuid.uuid4(), status="provider_error")
        with (
            patch.object(psychosocial_service.psychosocial_trace, "start", return_value=trace),
            patch.object(psychosocial_service.psychosocial_trace, "mark_failed") as mark_failed,
            patch.object(
                psychosocial_service,
                "get_llm_provider",
                side_effect=RuntimeError("provider down"),
            ),
        ):
            outcome = psychosocial_service.extract_and_store(
                db,
                uuid.uuid4(),
                "Me han echado de casa y no tengo a dónde ir esta noche.",
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        mark_failed.assert_called_once()
        self.assertEqual(outcome.status, "provider_error")
        self.assertEqual(outcome.observation_ids, [])


class ProfileArithmeticTests(unittest.TestCase):
    def test_no_observations_means_unknown_not_safe(self):
        profile = build_profile([])
        self.assertFalse(profile.available)
        self.assertIsNone(profile.support_index)
        self.assertIsNone(profile.material_adversity_index)
        # None, not False: "no data" must never satisfy a threshold.
        self.assertIsNone(profile.support_is_low)
        self.assertIsNone(profile.interpersonal_risk_is_high)

    def test_support_index_is_inverted_so_higher_is_better(self):
        strong = build_profile([_obs("apoyo_social", "protector"), _obs("familia", "protector")])
        weak = build_profile([_obs("apoyo_social", "riesgo_alto"), _obs("familia", "riesgo_alto")])
        self.assertEqual(strong.support_index, 1.0)
        self.assertEqual(weak.support_index, 0.0)
        self.assertTrue(weak.support_is_low)
        self.assertFalse(strong.support_is_low)

    def test_low_confidence_readings_are_shown_but_not_scored(self):
        profile = build_profile([_obs("economia", "riesgo_alto", confidence=0.2)])
        self.assertIn("economia", profile.domains)
        self.assertFalse(profile.domains["economia"].counts_for_scoring)
        self.assertIsNone(profile.material_adversity_index)

    def test_interpersonal_index_follows_catalogue_weights(self):
        profile = build_profile(
            [
                _obs("carga_percibida", "riesgo_alto", days_ago=1),
                _obs("pertenencia_frustrada", "riesgo_alto", days_ago=1),
                _obs("aislamiento", "riesgo_alto", days_ago=1),
            ]
        )
        self.assertEqual(profile.interpersonal_risk_index, 1.0)
        self.assertTrue(profile.interpersonal_risk_is_high)
        self.assertTrue(profile.interpersonal_risk_is_live)

    def test_old_interpersonal_risk_is_high_but_not_live(self):
        profile = build_profile(
            [
                _obs("carga_percibida", "riesgo_alto", days_ago=90),
                _obs("pertenencia_frustrada", "riesgo_alto", days_ago=90),
            ]
        )
        self.assertTrue(profile.interpersonal_risk_is_high)
        self.assertFalse(profile.interpersonal_risk_is_live)
        self.assertEqual(profile.interpersonal_recent_evidence, [])

    def test_acute_rupture_needs_recent_worsening_not_just_a_bad_state(self):
        chronic = build_profile([_obs("vivienda", "riesgo_alto", direction="estable", days_ago=60)])
        acute = build_profile([_obs("vivienda", "riesgo_alto", direction="empeora", days_ago=2)])
        self.assertFalse(chronic.has_acute_rupture)
        self.assertEqual(acute.acute_deterioration, ["vivienda"])

    def test_leave_taking_signal_expires_with_the_window(self):
        fresh = build_profile([_obs("senales_despedida", "riesgo_moderado", days_ago=3)])
        old = build_profile([_obs("senales_despedida", "riesgo_moderado", days_ago=40)])
        self.assertTrue(fresh.has_leave_taking_signal)
        self.assertFalse(old.has_leave_taking_signal)

    def test_professional_declaration_outranks_a_newer_model_reading(self):
        declared = _obs("vivienda", "protector", recorded_by="professional", days_ago=5)
        inferred = _obs("vivienda", "riesgo_alto", days_ago=1)
        profile = build_profile([declared, inferred])
        # The human's version is the current picture...
        self.assertEqual(profile.domains["vivienda"].state, "protector")
        self.assertTrue(profile.domains["vivienda"].is_declared)
        # ...and the contradicting reading is surfaced, not swallowed.
        self.assertEqual(profile.pending_update_domains, ["vivienda"])

    def test_stale_domains_are_flagged_rather_than_dropped(self):
        profile = build_profile([_obs("economia", "riesgo_moderado", days_ago=200)])
        self.assertEqual(profile.stale_domains, ["economia"])
        self.assertIn("economia", profile.domains)


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
    def __init__(self, checkins=None, psychosocial=None):
        self.rows_by_model = {
            CheckIn: checkins or [],
            PsychosocialObservation: psychosocial or [],
        }

    def query(self, model):
        return _FakeQuery(self.rows_by_model.get(model, []))


def _structural(score=0.85, band="stable"):
    return baseline.StructuralScoreResult(
        score=score,
        confidence_band=band,
        z_scores={key: 0.1 for key in baseline.VARIABLES},
        baseline_n=8,
        recent_n=4,
        baseline_stats={key: {"mean": 5.0, "std": 1.0, "n": 8} for key in baseline.VARIABLES},
        recent_means={key: 5.1 for key in baseline.VARIABLES},
        composite_z=0.1,
    )


def _linguistic(*, eligible=True, indirect=False, rumination=0.2, negative_valence=0.2):
    signal_id = uuid.uuid4() if eligible else None
    return {
        "_signal_uuid": signal_id,
        "signal_id": str(signal_id) if signal_id else None,
        "signal_timestamp": datetime.utcnow().isoformat() if signal_id else None,
        "eligible_for_risk": eligible,
        "freshness_window_hours": 12,
        "ideation_direct": False,
        "ideation_indirect": indirect if eligible else False,
        "consumption_crisis": False,
        "rumination_score": rumination if eligible else None,
        "negative_valence": negative_valence if eligible else None,
        "raw": {"rumination_score": rumination, "ideation_indirect": indirect},
    }


def _persistence(days, required):
    return {
        "band": "unstable",
        "window_days": required,
        "required_distinct_days": required,
        "observed_distinct_days": days,
        "observed_dates": [],
        "passed": days >= required,
    }


class PsychosocialRiskRuleTests(unittest.TestCase):
    """The rules, exercised one at a time against a stable, unremarkable patient.

    Structural score is deliberately 'stable' and the linguistic flags are
    deliberately quiet in every case below: whatever fires, fires because of
    the social context and nothing else.
    """

    def _calculate(self, *, observations=None, linguistic=None, craving=(3, 3, 3), structural=None):
        checkins = [
            SimpleNamespace(
                id=uuid.uuid4(),
                sleep_hours=7.0,
                craving=value,
                created_at=datetime.utcnow(),
            )
            for value in reversed(craving)
        ]
        persistence_values = {
            1: _persistence(0, 1),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_CONVERGENT: _persistence(0, 3),
            risk_engine.STRUCTURAL_PERSISTENCE_DAYS_N3_ALONE: _persistence(0, 5),
        }
        profile = build_profile(observations or [])
        with (
            patch.object(baseline, "compute_structural_score", return_value=structural or _structural()),
            patch.object(risk_engine, "_linguistic_flags", return_value=linguistic or _linguistic()),
            patch.object(risk_engine, "_facts_in_categories", side_effect=[[], []]),
            patch.object(
                risk_engine,
                "_persistence_detail",
                side_effect=lambda _db, _uid, _band, days: persistence_values[days],
            ),
            patch.object(risk_engine, "_psychosocial_profile", return_value=profile),
        ):
            return risk_engine.calculate_risk_level(_FakeDb(checkins), uuid.uuid4())

    def test_patient_without_psychosocial_data_is_unaffected(self):
        decision = self._calculate()
        self.assertEqual(decision.level, 0)
        self.assertEqual(decision.triggering_rules, ["N0_estable"])
        psychosocial_rules = [
            rule
            for rule in decision.calculation_trace["rules"]
            if rule["code"].endswith(("psicosocial", "despedida", "interpersonal_alto", "recaida_contextual"))
        ]
        self.assertTrue(psychosocial_rules)
        # Not evaluable, never "did not match": absence of data is not evidence.
        for rule in psychosocial_rules:
            self.assertIsNone(rule["matched"], rule["code"])
            self.assertEqual(rule["status"], "not_evaluable")

    def test_innocuous_convergence_reaches_level_4(self):
        decision = self._calculate(
            observations=[
                _obs("carga_percibida", "riesgo_alto", days_ago=2, summary="Dice que su familia estaría mejor sin él."),
                _obs("pertenencia_frustrada", "riesgo_alto", days_ago=2),
                _obs("senales_despedida", "riesgo_moderado", days_ago=1, summary="Ha regalado su guitarra."),
            ],
            linguistic=_linguistic(indirect=True),
        )
        self.assertEqual(decision.level, 4)
        self.assertEqual(decision.triggering_rules, ["N4_convergencia_interpersonal_despedida"])

    def test_the_same_convergence_without_leave_taking_stays_at_3(self):
        decision = self._calculate(
            observations=[
                _obs("carga_percibida", "riesgo_alto", days_ago=2),
                _obs("pertenencia_frustrada", "riesgo_alto", days_ago=2),
            ],
            linguistic=_linguistic(indirect=True),
        )
        self.assertEqual(decision.level, 3)
        self.assertEqual(decision.triggering_rules, ["N3_riesgo_interpersonal_alto"])

    def test_acute_rupture_plus_subtle_signal_raises_professional_alarm(self):
        decision = self._calculate(
            observations=[
                _obs("vivienda", "riesgo_alto", direction="empeora", days_ago=1),
                _obs("apoyo_social", "riesgo_moderado", direction="empeora", days_ago=2),
            ],
            linguistic=_linguistic(rumination=0.75),
        )
        self.assertEqual(decision.level, 3)
        self.assertEqual(decision.triggering_rules, ["N3_desconexion_psicosocial_aguda"])

    def test_acute_rupture_alone_is_prevention_not_alarm(self):
        decision = self._calculate(
            observations=[_obs("vivienda", "riesgo_alto", direction="empeora", days_ago=1)],
            linguistic=_linguistic(rumination=0.1, negative_valence=0.1),
        )
        self.assertEqual(decision.level, 2)
        self.assertEqual(decision.triggering_rules, ["N2_vulnerabilidad_psicosocial"])

    def test_relapse_context_needs_craving_actually_rising(self):
        observations = [
            _obs("contexto_consumo", "riesgo_alto", days_ago=2),
            _obs("convivencia", "riesgo_alto", days_ago=2),
            _obs("empleo_ocupacion", "riesgo_moderado", days_ago=3),
        ]
        flat = self._calculate(observations=observations, craving=(4, 4, 4))
        rising = self._calculate(observations=observations, craving=(2, 5, 8))
        self.assertNotEqual(flat.triggering_rules, ["N3_riesgo_recaida_contextual"])
        self.assertEqual(rising.level, 3)
        self.assertEqual(rising.triggering_rules, ["N3_riesgo_recaida_contextual"])

    def test_protective_context_does_not_raise_anything(self):
        decision = self._calculate(
            observations=[
                _obs("apoyo_social", "protector", days_ago=1),
                _obs("familia", "protector", days_ago=1),
                _obs("vivienda", "neutro", days_ago=1),
            ]
        )
        self.assertEqual(decision.level, 0)

    def test_decision_stores_the_quotes_it_used(self):
        decision = self._calculate(
            observations=[
                _obs("carga_percibida", "riesgo_alto", days_ago=2, quote="solo les doy disgustos"),
                _obs("pertenencia_frustrada", "riesgo_alto", days_ago=2),
            ],
            linguistic=_linguistic(indirect=True),
        )
        snapshot = decision.calculation_trace["inputs"]["psychosocial"]
        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["domains"]["carga_percibida"]["evidence_quote"], "solo les doy disgustos")
        self.assertEqual(
            snapshot["thresholds"]["interpersonal_risk_high_min"],
            psychosocial_service.INTERPERSONAL_RISK_HIGH_MIN,
        )
        self.assertIn("psychosocial", decision.input_signals)


class PsychosocialExplanationTests(unittest.TestCase):
    def test_every_new_rule_has_a_therapist_facing_explanation(self):
        for code in (
            "N4_convergencia_interpersonal_despedida",
            "N3_desconexion_psicosocial_aguda",
            "N3_riesgo_interpersonal_alto",
            "N3_riesgo_recaida_contextual",
            "N2_vulnerabilidad_psicosocial",
        ):
            info = clinical_view.rule_info(code)
            self.assertEqual(info["family"], clinical_view.FAMILY_PSYCHOSOCIAL, code)
            self.assertTrue(info["plain"].strip(), code)
            self.assertTrue(info["what_now"].strip(), code)

    def test_high_structural_score_next_to_psychosocial_level_is_reconciled(self):
        assessment = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            alert_level=3,
            triggering_rules=["N3_desconexion_psicosocial_aguda"],
            assessment_reason="motivo",
            calculated_at=datetime(2026, 8, 16, 10, 0, 0),
            generated_alert_id=None,
            input_signals={"structural_score": 0.93, "confidence_band": "stable"},
            input_facts={},
            calculation_trace={"conclusion": {"selected_rule_code": "N3_desconexion_psicosocial_aguda"}},
        )
        explanation = clinical_view.level_explanation(assessment)
        self.assertEqual(explanation["driver_family"], clinical_view.FAMILY_PSYCHOSOCIAL)
        self.assertIn("CONTEXTO SOCIAL", explanation["headline"])
        self.assertIn("0.93", explanation["structural_reconciliation"])
        self.assertIn("no hay contradicción", explanation["structural_reconciliation"].lower())

    def test_evidence_for_a_psychosocial_decision_quotes_the_stored_snapshot(self):
        assessment = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            alert_level=3,
            triggering_rules=["N3_riesgo_interpersonal_alto"],
            calculated_at=datetime(2026, 8, 16, 10, 0, 0),
            input_facts={},
            input_signals={},
            calculation_trace={
                "conclusion": {"selected_rule_code": "N3_riesgo_interpersonal_alto"},
                "inputs": {
                    "psychosocial": {
                        "available": True,
                        "indices": {"interpersonal_risk_index": 0.83},
                        "interpersonal_recent_evidence": ["carga_percibida"],
                        "acute_deterioration": [],
                        "domains": {
                            "carga_percibida": {
                                "state": "riesgo_alto",
                                "summary": "Se ve como un lastre para su hermana.",
                                "evidence_quote": "solo les doy disgustos",
                                "source_type": "chat_message",
                                "source_id": str(uuid.uuid4()),
                                "observed_at": "2026-08-15T18:00:00+00:00",
                                "is_declared": False,
                            }
                        },
                    }
                },
            },
        )
        evidence = clinical_view.evidence_for_assessment(MagicMock(), assessment)
        self.assertEqual(evidence["kind"], "psicosocial")
        self.assertEqual(evidence["text"], "solo les doy disgustos")
        self.assertEqual(evidence["psychosocial_domains"][0]["label"], DOMAIN_BY_KEY["carga_percibida"].label)

    def test_view_groups_domains_and_proposes_what_to_ask(self):
        observations = [
            _obs("vivienda", "riesgo_alto", direction="empeora", days_ago=1),
            _obs("apoyo_social", "protector", days_ago=4),
            _obs("carga_percibida", "riesgo_alto", days_ago=2),
        ]
        db = MagicMock()
        with (
            patch.object(psychosocial_service, "current_observations", return_value=observations),
            patch.object(psychosocial_service, "history", return_value=observations),
        ):
            view = clinical_view.build_psychosocial_view(db, uuid.uuid4())

        self.assertTrue(view["available"])
        self.assertEqual([item["domain"] for item in view["acute_deterioration"]], ["vivienda"])
        self.assertEqual([item["domain"] for item in view["protective_domains"]], ["apoyo_social"])
        asked = [item["domain"] for item in view["session_questions"]]
        # Whatever is moving comes first, and every question carries its reason.
        self.assertEqual(asked[0], "vivienda")
        self.assertIn("carga_percibida", asked)
        self.assertTrue(all(item["question"] for item in view["session_questions"]))
        self.assertIn("Deterioro en los últimos 14 días", view["headline"])
        groups = {group["group"] for group in view["groups"]}
        self.assertIn("material", groups)
        self.assertIn("riesgo_interpersonal", groups)

    def test_view_says_so_when_nothing_is_known(self):
        db = MagicMock()
        with (
            patch.object(psychosocial_service, "current_observations", return_value=[]),
            patch.object(psychosocial_service, "history", return_value=[]),
        ):
            view = clinical_view.build_psychosocial_view(db, uuid.uuid4())
        self.assertFalse(view["available"])
        self.assertIn("Todavía no hay contexto psicosocial", view["headline"])
        self.assertEqual(view["groups"], [])


class PsychosocialMigrationContractTests(unittest.TestCase):
    def test_migration_hardens_both_tables(self):
        migration_path = (
            Path(__file__).resolve().parents[2]
            / "supabase"
            / "migrations"
            / "20260816120000_add_psychosocial_context.sql"
        )
        if not migration_path.exists():
            self.skipTest("Repository-level migration is outside the backend-only Docker build context")
        migration = migration_path.read_text(encoding="utf-8").lower()
        self.assertIn("psychosocial_observations", migration)
        self.assertIn("psychosocial_extraction_traces", migration)
        self.assertIn("force row level security", migration)
        self.assertIn("to psychdeep_backend", migration)
        self.assertIn("from public, anon, authenticated, service_role", migration)
        self.assertIn("set local role psychdeep_backend", migration)
        self.assertIn("revoke psychdeep_backend from postgres granted by postgres", migration)
        self.assertIn("membership was not removed", migration)
        self.assertIn("evidence_quote text", migration)


if __name__ == "__main__":
    unittest.main()
