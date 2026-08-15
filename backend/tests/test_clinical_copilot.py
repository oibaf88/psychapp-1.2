"""Tests for Agent 3, the therapist's clinical copilot.

Two properties matter most here:

  * the dossier handed to the model contains BOTH sources the patient
    writes into (chat and diary), because the therapist asked for a
    summary of "what the patient has told it";
  * a provider failure still produces a stored assistant turn explaining
    why, instead of a silent empty panel.
"""
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from app.models import (
    AlfaSignal,
    ChatMessage,
    CheckIn,
    ConfirmedFact,
    DiaryEntry,
    ProfessionalAlert,
    RiskAssessment,
    SafetyPlan,
    TherapistCopilotMessage,
)
from app.services import clinical_copilot


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, _limit):
        return self

    def offset(self, _offset):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def count(self):
        return len(self._rows)


class _Db:
    """Fake session that dispatches by model, like the real queries do."""

    def __init__(self, rows_by_model):
        self.rows_by_model = rows_by_model
        self.added = []
        self.commits = 0

    def query(self, model):
        return _Query(self.rows_by_model.get(model, []))

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        return None

    def get(self, _model, _pk):
        return None


def _patient():
    return SimpleNamespace(id=uuid.uuid4(), display_name="Paciente Demo", role="patient")


def _professional():
    return SimpleNamespace(id=uuid.uuid4(), display_name="Terapeuta", role="therapist")


def _populated_db():
    now = datetime(2026, 8, 14, 9, 0, 0)
    return _Db(
        {
            CheckIn: [
                SimpleNamespace(
                    created_at=now, mood=4, craving=7, sleep_hours=5.0, self_efficacy=3, notes="mal día"
                )
            ],
            DiaryEntry: [SimpleNamespace(created_at=now, content="Llevo tres noches sin dormir bien.")],
            ChatMessage: [
                SimpleNamespace(created_at=now, role="user", content="No sé cómo salir de esto."),
                SimpleNamespace(created_at=now, role="assistant", content="Estoy aquí contigo."),
            ],
            ConfirmedFact: [
                SimpleNamespace(
                    created_at=now, category="relapse", declared_by="user", content="Consumo el sábado."
                )
            ],
            AlfaSignal: [
                SimpleNamespace(
                    timestamp=now,
                    value={
                        "rumination_score": 0.8,
                        "negative_valence": 0.7,
                        "urgency_level": 0.4,
                        "ambivalence": 0.5,
                        "ideation_direct": False,
                        "ideation_indirect": True,
                        "consumption_crisis": False,
                        "short_rationale": "Rumiación alta.",
                    },
                )
            ],
            RiskAssessment: [],
            ProfessionalAlert: [],
            SafetyPlan: [],
            TherapistCopilotMessage: [],
        }
    )


class DossierTests(unittest.TestCase):
    def test_dossier_includes_both_chat_and_diary(self):
        text, counts = clinical_copilot.build_dossier_text(_populated_db(), _patient())
        self.assertIn("Llevo tres noches sin dormir bien.", text)
        self.assertIn("No sé cómo salir de esto.", text)
        self.assertEqual(counts["diary"], 1)
        self.assertEqual(counts["chat"], 2)

    def test_dossier_labels_signals_as_inferences_and_facts_as_facts(self):
        text, _counts = clinical_copilot.build_dossier_text(_populated_db(), _patient())
        self.assertIn("HECHOS CONFIRMADOS (HECHOS, no inferencias", text)
        self.assertIn("SEÑALES DEL AGENTE 2 (INFERENCIAS", text)

    def test_dossier_marks_who_wrote_each_chat_turn(self):
        text, _counts = clinical_copilot.build_dossier_text(_populated_db(), _patient())
        self.assertIn("PACIENTE: No sé cómo salir de esto.", text)
        self.assertIn("ASISTENTE: Estoy aquí contigo.", text)

    def test_empty_record_says_so_instead_of_omitting_sections(self):
        empty = _Db({model: [] for model in (CheckIn, DiaryEntry, ChatMessage, ConfirmedFact, AlfaSignal)})
        text, counts = clinical_copilot.build_dossier_text(empty, _patient())
        self.assertIn("No hay check-ins en la ventana.", text)
        self.assertIn("No hay entradas de diario en la ventana.", text)
        self.assertIn("No hay mensajes de chat en la ventana.", text)
        self.assertEqual(counts["chat"], 0)

    def test_long_text_is_clipped(self):
        self.assertTrue(clinical_copilot._clip("x" * 1000, 50).endswith("…"))
        self.assertEqual(len(clinical_copilot._clip("x" * 1000, 50)), 50)


class AskTests(unittest.TestCase):
    def test_successful_answer_is_persisted_with_context_counts(self):
        db = _populated_db()
        provider = SimpleNamespace(chat=lambda *_a, **_k: "Resumen del paciente.")
        with patch.object(clinical_copilot, "get_llm_provider", return_value=provider):
            answer = clinical_copilot.ask(
                db, professional=_professional(), patient=_patient(), question="¿Cómo está?"
            )
        self.assertEqual(answer.role, "assistant")
        self.assertEqual(answer.content, "Resumen del paciente.")
        self.assertIsNone(answer.error_kind)
        self.assertEqual(answer.context_counts["chat"], 2)

    def test_provider_failure_still_returns_an_explained_turn(self):
        db = _populated_db()

        def _boom(*_args, **_kwargs):
            raise RuntimeError("provider down")

        with patch.object(
            clinical_copilot, "get_llm_provider", return_value=SimpleNamespace(chat=_boom)
        ):
            answer = clinical_copilot.ask(
                db, professional=_professional(), patient=_patient(), question="¿Cómo está?"
            )
        self.assertEqual(answer.error_kind, "RuntimeError")
        self.assertIn("No he podido generar la respuesta", answer.content)
        self.assertIn("check-ins, diario, chat", answer.content)

    def test_empty_reply_is_treated_as_a_failure(self):
        db = _populated_db()
        with patch.object(
            clinical_copilot,
            "get_llm_provider",
            return_value=SimpleNamespace(chat=lambda *_a, **_k: "   "),
        ):
            answer = clinical_copilot.ask(
                db, professional=_professional(), patient=_patient(), question="¿Cómo está?"
            )
        self.assertEqual(answer.error_kind, "RuntimeError")

    def test_question_turn_is_stored_before_the_answer(self):
        db = _populated_db()
        with patch.object(
            clinical_copilot,
            "get_llm_provider",
            return_value=SimpleNamespace(chat=lambda *_a, **_k: "ok"),
        ):
            clinical_copilot.ask(db, professional=_professional(), patient=_patient(), question="hola")
        roles = [row.role for row in db.added if isinstance(row, TherapistCopilotMessage)]
        self.assertEqual(roles, ["user", "assistant"])

    def test_summary_is_marked_as_a_summary(self):
        db = _populated_db()
        with patch.object(
            clinical_copilot,
            "get_llm_provider",
            return_value=SimpleNamespace(chat=lambda *_a, **_k: "resumen"),
        ):
            answer = clinical_copilot.summarize(db, professional=_professional(), patient=_patient())
        self.assertEqual(answer.kind, "summary")

    def test_copilot_never_writes_clinical_records(self):
        """Agent 3 must not be able to move a patient's alert level."""
        db = _populated_db()
        with patch.object(
            clinical_copilot,
            "get_llm_provider",
            return_value=SimpleNamespace(chat=lambda *_a, **_k: "ok"),
        ):
            clinical_copilot.ask(db, professional=_professional(), patient=_patient(), question="hola")
        for row in db.added:
            self.assertIsInstance(row, TherapistCopilotMessage)


if __name__ == "__main__":
    unittest.main()
