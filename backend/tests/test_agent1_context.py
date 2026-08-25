"""What Agent 1 is told, and what it is no longer told.

Two of its own instructions were unfollowable. The prompt says never to
overwrite confirmed facts, and to suggest reviewing the safety plan "si ya
lo tiene" — and the model could see neither. Meanwhile it received the
Python repr of the engine's entire input dictionary, inside a prompt that
forbids revealing any of it.
"""
import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.content.prompts import AGENT1_CRISIS_INSTRUCTION, AGENT1_SYSTEM_PROMPT
from app.models import CheckIn, ConfirmedFact, SafetyPlan
from app.services import agent1_context


class _Db:
    """Dispatches by model, like the real session does for these queries."""

    def __init__(self, facts=None, plan=None, checkins=None):
        self.rows = {ConfirmedFact: facts or [], CheckIn: checkins or [], SafetyPlan: [plan] if plan else []}

    def query(self, model):
        return _Q(self.rows.get(model, []))


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *_a):
        return self

    def order_by(self, *_a):
        return self

    def limit(self, _n):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


def _fact(category="relapse", content="Recaí el martes."):
    return SimpleNamespace(category=category, content=content, created_at=datetime.utcnow())


def _checkin():
    return SimpleNamespace(
        mood=4, craving=7, sleep_hours=5.0, self_efficacy=3, created_at=datetime.utcnow()
    )


def _plan(**kwargs):
    base = dict(
        warning_signs=None, coping_strategies=None, social_supports=None, reasons_to_live=None
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _assessment(level=0):
    return SimpleNamespace(alert_level=level, assessment_reason="x", input_signals={"secret": 1})


def _build(db, *, level=0, profile=None, social=""):
    with patch.object(agent1_context.profile_service, "get", return_value=profile), \
         patch.object(agent1_context, "_psychosocial_block", return_value=social):
        return agent1_context.build(db, uuid.uuid4(), _assessment(level), in_crisis=level >= 3)


class NoRawEngineDumpTests(unittest.TestCase):
    def test_the_engine_internals_do_not_reach_the_prompt(self):
        """Thresholds, formulas, z-scores and rule predicates were all in it."""
        block = _build(_Db())
        for leak in ("input_signals", "rumination_threshold_exceeded", "structural_score", "secret"):
            self.assertNotIn(leak, block)

    def test_the_alert_number_is_never_in_the_block(self):
        for level in (0, 1, 2, 3, 4):
            block = _build(_Db(), level=level)
            self.assertNotIn("nivel 4", block.lower())
            self.assertNotIn("alert_level", block)

    def test_the_state_reaches_it_as_words(self):
        self.assertIn("emergencia", _build(_Db(), level=4))
        self.assertIn("habitual", _build(_Db(), level=0))


class FactsAreVisibleTests(unittest.TestCase):
    """"No inventas ni sobrescribes hechos confirmados" was unfollowable."""

    def test_active_declarations_appear(self):
        block = _build(_Db(facts=[_fact()]))
        self.assertIn("Recaí el martes.", block)
        self.assertIn("Recaída declarada", block)

    def test_they_are_labelled_as_facts_not_inferences(self):
        block = _build(_Db(facts=[_fact()]))
        self.assertIn("Son HECHOS, no inferencias", block)

    def test_no_facts_adds_no_section(self):
        self.assertNotIn("HECHOS DECLARADOS", _build(_Db()))


class SafetyPlanIsVisibleTests(unittest.TestCase):
    """"Revisa tu plan de seguridad si ya lo tiene" was unfollowable too."""

    def test_absence_is_stated_explicitly(self):
        block = _build(_Db())
        self.assertIn("no tiene ninguno", block)
        self.assertIn("como si existiera", block)

    def test_the_content_is_shown_when_there_is_one(self):
        block = _build(_Db(plan=_plan(reasons_to_live="Mi perra.")))
        self.assertIn("Mi perra.", block)
        self.assertIn("Razones para vivir", block)

    def test_an_empty_plan_is_distinguished_from_no_plan(self):
        block = _build(_Db(plan=_plan()))
        self.assertIn("existe pero está vacío", block)

    def test_the_agent_is_told_not_to_rewrite_it(self):
        block = _build(_Db(plan=_plan(coping_strategies="Salir a andar.")))
        self.assertIn("nunca reescribírselo", block)


class DirectionTests(unittest.TestCase):
    """Proactive and directed, without turning into a questionnaire."""

    def _profile(self, threads=None, portrait=None):
        return SimpleNamespace(open_threads=threads, portrait=portrait)

    def test_open_threads_reach_the_turn(self):
        block = _build(_Db(), profile=self._profile([{"topic": "Lo del piso", "note": "quedó a medias"}]))
        self.assertIn("Lo del piso", block)
        self.assertIn("quedó a medias", block)

    def test_the_agent_is_told_to_offer_not_interrogate(self):
        block = _build(_Db(), profile=self._profile([{"topic": "El trabajo"}]))
        self.assertIn("OFRECE, no interrogues", block)

    def test_the_agenda_is_dropped_in_crisis(self):
        """Arriving with topics to cover is the wrong thing to do to
        someone in crisis, and the crisis instruction already says so."""
        threads = [{"topic": "Lo del piso"}]
        calm = _build(_Db(), level=0, profile=self._profile(threads))
        crisis = _build(_Db(), level=4, profile=self._profile(threads))
        self.assertIn("Lo del piso", calm)
        self.assertNotIn("Lo del piso", crisis)
        self.assertNotIn("TEMAS ABIERTOS", crisis)

    def test_the_prompt_forbids_pressing(self):
        self.assertIn("no un obstáculo", AGENT1_SYSTEM_PROMPT)
        self.assertIn("Una sola pregunta por turno", AGENT1_SYSTEM_PROMPT)

    def test_the_prompt_says_direction_is_off_in_crisis(self):
        self.assertIn("Nada de esto se aplica cuando el nivel es alto", AGENT1_SYSTEM_PROMPT)

    def test_the_crisis_instruction_still_forbids_multiple_questions(self):
        self.assertIn("preguntas múltiples", AGENT1_CRISIS_INSTRUCTION)

    def test_a_profile_with_no_threads_adds_no_agenda(self):
        self.assertNotIn("TEMAS ABIERTOS", _build(_Db(), profile=self._profile([])))


class RobustnessTests(unittest.TestCase):
    """A thin reply beats no reply: this must never raise into the chat."""

    def test_a_failing_query_degrades_instead_of_raising(self):
        class _Boom:
            def query(self, _m):
                raise RuntimeError("database gone")

        with patch.object(agent1_context.profile_service, "get", return_value=None), \
             patch.object(agent1_context, "_psychosocial_block", return_value=""):
            block = agent1_context.build(_Boom(), uuid.uuid4(), _assessment(), in_crisis=False)
        self.assertIsInstance(block, str)

    def test_a_non_string_portrait_is_ignored_rather_than_crashing(self):
        """It is a free-text column; a bad value must not kill the reply."""
        block = _build(_Db(), profile=SimpleNamespace(open_threads=None, portrait=object()))
        self.assertIsInstance(block, str)
        self.assertNotIn("QUIÉN ES ESTA PERSONA", block)

    def test_a_real_portrait_is_included(self):
        block = _build(_Db(), profile=SimpleNamespace(open_threads=None, portrait="Vive sola con su perra."))
        self.assertIn("Vive sola con su perra.", block)
        self.assertIn("nunca prevalece sobre lo que diga hoy", block)


class ProfilePanelTests(unittest.TestCase):
    """A model's summary of a patient that nobody can correct is one
    nobody should trust."""

    def test_the_panel_payload_says_whether_the_baseline_is_in_use(self):
        from app.services import profile as profile_service

        empty = profile_service.as_dict(None)
        self.assertFalse(empty["baseline_is_usable"])
        self.assertEqual(empty["minimum_signals_for_baseline"],
                         profile_service.MIN_SIGNALS_FOR_LINGUISTIC_BASELINE)

    def test_numbers_alone_do_not_mean_the_engine_is_using_them(self):
        from app.services import profile as profile_service

        thin = SimpleNamespace(
            portrait=None, previous_portrait=None, portrait_version=0,
            portrait_updated_at=None, portrait_edited_by=None, open_threads=None,
            linguistic_baseline={"rumination_score": {"mean": 0.4, "std": 0.1, "n": 3}},
            linguistic_baseline_n=3,
        )
        payload = profile_service.as_dict(thin)
        self.assertIsNotNone(payload["linguistic_baseline"])
        self.assertFalse(payload["baseline_is_usable"])

    def test_editing_is_gated_and_audited(self):
        import inspect

        from app.routers import professional

        source = inspect.getsource(professional.update_patient_profile)
        self.assertIn("_require_fact_access", source)
        self.assertIn("audit.log", source)

    def test_editing_the_profile_does_not_re_run_the_engine(self):
        """Nothing here is evidence the deterministic engine reads."""
        import inspect

        from app.routers import professional

        self.assertNotIn(
            "risk_engine.run_and_persist", inspect.getsource(professional.update_patient_profile)
        )

    def test_a_clinician_edit_is_recorded_as_such(self):
        import inspect

        from app.services import profile as profile_service

        self.assertIn(
            "portrait_edited_by = actor_id",
            inspect.getsource(profile_service.set_portrait_by_clinician),
        )


if __name__ == "__main__":
    unittest.main()
