"""The false positive that started the reconfiguration, as a test.

Someone said they had decided to change their life for the better, and the
system raised a suicide-crisis alert. This file pins the fixes and, just as
importantly, pins that they did not buy the improvement by going quiet: the
same words with a history of hopelessness behind them must still raise a
review.
"""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from app.content import psychosocial_catalog
from app.content.prompts import AGENT4_SYSTEM_PROMPT, ANALYZER_SYSTEM_PROMPT
from app.services import psychosocial

CHANGE_TALK = "He decidido cambiar de vida, voy a poner mis cosas en orden y buscar trabajo"


def _observation(**kwargs):
    base = dict(
        domain="connectedness",
        category="future_plans",
        valence="protective",
        intensity=0.6,
        confidence=0.8,
        is_change=True,
        summary="Plan de buscar trabajo.",
        quote="voy a poner mis cosas en orden y buscar trabajo",
    )
    base.update(kwargs)
    return psychosocial.PsychosocialObservationIn(**base)


class ChangeTalkCounterweightTests(unittest.TestCase):
    """The prompt pushed in one direction only.

    It told the model the author was at risk, to look for double meanings
    and indirect hopelessness, and not to soften anything — with no line
    anywhere about change talk, no negative examples, and nothing about what
    a false positive costs. Given "he decidido cambiar de vida" and no
    history, a model cannot tell a turning point from a euphemism.
    """

    def test_the_prompt_states_what_a_false_positive_costs(self):
        self.assertIn("falso positivo", ANALYZER_SYSTEM_PROMPT)

    def test_the_real_sentence_appears_as_a_worked_negative_example(self):
        self.assertIn("He decidido cambiar de vida", ANALYZER_SYSTEM_PROMPT)

    def test_the_counterweight_does_not_tell_the_model_to_soften_ideation(self):
        """Trading false positives for false negatives would be much worse."""
        self.assertIn("no te pide suavizar nada", ANALYZER_SYSTEM_PROMPT)
        # The original instruction is still there and still governs.
        self.assertIn("no ocultarlo ni suavizarlo", ANALYZER_SYSTEM_PROMPT)

    def test_closure_and_project_are_distinguished(self):
        self.assertIn("CIERRE, no proyecto", ANALYZER_SYSTEM_PROMPT)
        self.assertIn("SEGUIR o para IRSE", ANALYZER_SYSTEM_PROMPT)

    def test_forward_looking_text_is_pointed_at_future_plans(self):
        self.assertIn("prefiere `future_plans`", ANALYZER_SYSTEM_PROMPT)

    def test_the_retired_agent4_prompt_is_untouched(self):
        """Its sha256 is stamped on traces already in the database."""
        self.assertNotIn("CIERRE, no proyecto", AGENT4_SYSTEM_PROMPT)
        self.assertNotIn("falso positivo", AGENT4_SYSTEM_PROMPT)


class ProtectiveValenceTests(unittest.TestCase):
    """PROTECTIVE_CATEGORIES was documented as a check and never applied.

    An adverse reading is the one that moves an index upward, so a model
    that got the valence backwards would raise a patient's risk for exactly
    the things that lower it — having plans, having support, having a home.
    """

    def test_a_protective_category_called_adverse_is_dropped(self):
        self.assertFalse(_coherent := psychosocial._coherent(_observation(valence="risk")))

    def test_the_same_category_read_correctly_is_kept(self):
        self.assertTrue(psychosocial._coherent(_observation(valence="protective")))

    def test_a_genuinely_adverse_category_is_unaffected(self):
        self.assertTrue(
            psychosocial._coherent(
                _observation(category="loss_of_routine", valence="risk")
            )
        )

    def test_every_protective_category_is_a_real_catalogue_category(self):
        """A typo here would silently disable the check for that category."""
        for category in psychosocial_catalog.PROTECTIVE_CATEGORIES:
            self.assertIn(category, psychosocial_catalog.CATEGORY_KEYS, category)

    def test_the_check_survives_the_full_row_builder(self):
        rows = psychosocial.build_observation_rows(
            {
                "has_psychosocial_content": True,
                "observations": [
                    {
                        "domain": "connectedness",
                        "category": "future_plans",
                        "valence": "risk",
                        "intensity": 0.9,
                        "confidence": 0.9,
                        "is_change": True,
                        "summary": "Planes de futuro leídos como adversidad.",
                        "quote": "voy a poner mis cosas en orden y buscar trabajo",
                    }
                ],
            },
            user_id=uuid.uuid4(),
            text=CHANGE_TALK,
            source_type="chat_message",
            source_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
        )
        self.assertEqual(rows, [])


class LeaveTakingWeightTests(unittest.TestCase):
    """The comment said one thing and the number said another."""

    def test_leave_taking_moves_no_index(self):
        """0.95 was nearly the maximum, directly under a comment saying the
        domain never moves an index on its own."""
        self.assertEqual(psychosocial_catalog.DOMAIN_WEIGHTS["leave_taking"], 0.0)

    def test_the_convergence_rule_does_not_read_the_weight(self):
        """Zeroing the weight must not disarm the N4 leg it exists for."""
        import inspect

        source = inspect.getsource(psychosocial.PsychosocialAssessment.has_leave_taking_signal.fget)
        self.assertNotIn("weight", source)

    def test_a_leave_taking_observation_still_registers(self):
        self.assertTrue(
            psychosocial._coherent(
                _observation(
                    domain="leave_taking",
                    category="affairs_in_order",
                    valence="risk",
                    quote="dejo los papeles listos por si acaso",
                )
            )
        )


class AmbivalenceIsRecordedTests(unittest.TestCase):
    """It was collected on every analysis and thrown away.

    It never moves a level and is not meant to. It is what a therapist
    reads *next to* a flag — and in the false-positive case it is the field
    that would have said the model also detected a desire to change.
    """

    def test_the_engine_carries_ambivalence_out_of_the_signal(self):
        from app.services import risk_engine

        signal = SimpleNamespace(
            id=uuid.uuid4(),
            timestamp=__import__("datetime").datetime.utcnow(),
            value={
                "ambivalence": 0.9,
                "urgency_level": 0.2,
                "emotional_complexity": "high",
                "short_rationale": "Decisión de cambio con dudas.",
                "ideation_direct": False,
            },
        )

        class _Q:
            def filter(self, *_a):
                return self

            def order_by(self, *_a):
                return self

            def first(self):
                return signal

        flags = risk_engine._linguistic_flags(SimpleNamespace(query=lambda _m: _Q()), uuid.uuid4())
        self.assertEqual(flags["ambivalence"], 0.9)
        self.assertEqual(flags["emotional_complexity"], "high")
        self.assertEqual(flags["short_rationale"], "Decisión de cambio con dudas.")

    def test_it_reaches_the_persisted_input_signals(self):
        import inspect

        from app.services import risk_engine

        source = inspect.getsource(risk_engine)
        self.assertIn('"ambivalence": ling.get("ambivalence")', source)

    def test_it_is_recorded_beside_the_flags_not_as_a_rule(self):
        """Nothing may start deciding a level from ambivalence."""
        import inspect

        from app.services import risk_engine

        for line in inspect.getsource(risk_engine).splitlines():
            if "ambivalence" in line and ("if " in line or "and " in line or "or " in line):
                self.assertIn("get(", line, f"ambivalence used in a condition: {line.strip()}")


if __name__ == "__main__":
    unittest.main()


class SignalRefutationTests(unittest.TestCase):
    """The piece that was missing entirely.

    Psychosocial observations had adjudication since they existed. Linguistic
    signals had none: no endpoint set `AlfaSignal.is_active = False`, so a
    wrong flag went on firing its rule on every evaluation for the whole
    freshness window and there was no way to say it was wrong.
    """

    def _signal(self, signal_type="linguistic_analysis"):
        return SimpleNamespace(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            signal_type=signal_type,
            is_active=True,
            superseded_by_fact=None,
        )

    class _Db:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

        def flush(self):
            for row in self.added:
                if getattr(row, "id", None) is None:
                    row.id = uuid.uuid4()

        def commit(self):
            self.flush()

        def refresh(self, _row):
            pass

    def test_refuting_deactivates_the_signal(self):
        from app.services import signals

        db, signal = self._Db(), self._signal()
        signals.refute(db, signal, actor_id=uuid.uuid4(), actor_role="therapist",
                       reason="Estaba hablando de cambiar de vida, no de despedirse.")
        self.assertFalse(signal.is_active)

    def test_the_engine_needs_no_change_to_honour_it(self):
        """Every query the engine makes already filters is_active."""
        import inspect

        from app.services import risk_engine

        source = inspect.getsource(risk_engine)
        self.assertIn("AlfaSignal.is_active == True", source)

    def test_the_signal_row_is_kept_not_deleted(self):
        """Deleting it would take the trace, the source text and the alert
        with it — the whole reason a clinician can review the decision."""
        from app.services import signals

        db, signal = self._Db(), self._signal()
        signals.refute(db, signal, actor_id=uuid.uuid4(), actor_role="therapist", reason="Mal leído.")
        self.assertIsNotNone(signal.id)
        self.assertIsNotNone(signal.superseded_by_fact)

    def test_a_correction_fact_finally_does_something(self):
        """A `correction` fact used to be stored and ignored by the engine."""
        from app.models import ConfirmedFact
        from app.services import signals

        db, signal = self._Db(), self._signal()
        result = signals.refute(db, signal, actor_id=uuid.uuid4(), actor_role="therapist",
                                reason="No era una despedida.")
        fact = next(r for r in db.added if isinstance(r, ConfirmedFact))
        self.assertEqual(fact.category, "correction")
        self.assertEqual(fact.content, "No era una despedida.")
        self.assertEqual(signal.superseded_by_fact, fact.id)
        self.assertIs(result.fact, fact)

    def test_a_refutation_without_a_reason_is_refused(self):
        """Unexplained, it is indistinguishable from a mis-click later."""
        from app.services import signals

        db, signal = self._Db(), self._signal()
        with self.assertRaises(ValueError):
            signals.refute(db, signal, actor_id=uuid.uuid4(), actor_role="therapist", reason="   ")
        self.assertTrue(signal.is_active)

    def test_arithmetic_signals_cannot_be_refuted(self):
        """A structural_score is not a reading that can be wrong about the
        person: if the numbers are wrong, the check-ins need correcting."""
        from app.services import signals

        db, signal = self._Db(), self._signal(signal_type="structural_score")
        with self.assertRaises(signals.SignalNotRefutable):
            signals.refute(db, signal, actor_id=uuid.uuid4(), actor_role="therapist", reason="x")
        self.assertTrue(signal.is_active)

    def test_a_refutation_can_be_undone_but_the_statement_stays(self):
        from app.services import signals

        db, signal = self._Db(), self._signal()
        signals.refute(db, signal, actor_id=uuid.uuid4(), actor_role="therapist", reason="Mal leído.")
        signals.restore(db, signal, actor_id=uuid.uuid4())
        self.assertTrue(signal.is_active)
        self.assertIsNone(signal.superseded_by_fact)
        # The fact a person wrote is not retracted by the system.
        from app.models import ConfirmedFact

        self.assertTrue(any(isinstance(r, ConfirmedFact) for r in db.added))

    def test_the_endpoint_is_gated_and_re_evaluates(self):
        import inspect

        from app.routers import professional

        source = inspect.getsource(professional.refute_linguistic_signal)
        self.assertIn("_require_fact_access", source)
        self.assertIn("risk_engine.run_and_persist", source)
        self.assertIn("audit.log", source)


class StillEscalatesTests(unittest.TestCase):
    """The fixes must not have been bought by going quiet.

    Change talk stops being read as closure. Closure must still be read as
    closure, and the N4 convergence rule must be exactly as reachable as it
    was.
    """

    def test_closure_language_is_still_a_leave_taking_category(self):
        self.assertIn("affairs_in_order", psychosocial_catalog.DOMAIN_CATEGORIES["leave_taking"])
        self.assertIn(
            "sudden_calm_after_hopelessness",
            psychosocial_catalog.DOMAIN_CATEGORIES["leave_taking"],
        )

    def test_leave_taking_is_not_in_the_protective_set(self):
        for category in psychosocial_catalog.DOMAIN_CATEGORIES["leave_taking"]:
            self.assertNotIn(category, psychosocial_catalog.PROTECTIVE_CATEGORIES)

    def test_the_n4_convergence_rule_is_unchanged(self):
        """Its three legs still have to converge; nothing here relaxed it."""
        import inspect

        from app.services import risk_engine

        source = inspect.getsource(risk_engine)
        self.assertIn("ideation_indirect and interpersonal_live and leave_taking", source)

    def test_direct_ideation_still_reaches_level_four(self):
        import inspect

        from app.services import risk_engine

        self.assertIn("N4_senal_linguistica_ideacion_directa", inspect.getsource(risk_engine))
