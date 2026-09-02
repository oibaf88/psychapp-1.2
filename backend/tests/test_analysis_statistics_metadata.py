"""Extraction metadata survives persistence without inventing missing flags."""
import unittest
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import conversation


LINGUISTIC = {
    "rumination_score": 0.2,
    "negative_valence": 0.3,
    "urgency_level": 0.1,
    "ideation_indirect": False,
    "ideation_direct": False,
    "consumption_crisis": False,
    "ambivalence": 0.2,
    "emotional_complexity": "low",
    "short_rationale": "Texto sintético sin señales agudas.",
}
SYNTHETIC_TEXT = "Hoy he hablado sobre mi situación y mis actividades habituales, sin aportar más detalles concretos."


class AnalysisStatisticsMetadataTests(unittest.TestCase):
    def analyze(self, block, *, text=SYNTHETIC_TEXT):
        db = MagicMock()
        db.refresh.side_effect = lambda row: setattr(row, "id", row.id or uuid.uuid4())
        trace = SimpleNamespace(id=uuid.uuid4(), status="started")
        provider = MagicMock()
        provider.analyze_structured.return_value = SimpleNamespace(
            value={"linguistic": LINGUISTIC, "psychosocial": block},
            metadata=SimpleNamespace(),
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(conversation.agent2_trace, "start", return_value=trace))
            stack.enter_context(patch.object(conversation.agent2_trace, "mark_succeeded"))
            stack.enter_context(patch.object(conversation, "get_llm_provider", return_value=provider))
            stack.enter_context(patch.object(conversation.profile_service, "get", return_value=None))
            stack.enter_context(patch.object(conversation.profile_service, "apply_analyzer_update"))
            stack.enter_context(patch.object(conversation.profile_service, "refresh_linguistic_baseline"))
            result = conversation.analyze_text_and_store(
                db, uuid.uuid4(), text, source_type="chat_message",
                source_id=uuid.uuid4(), correlation_id=uuid.uuid4(),
            )
        self.assertEqual(result.status, "succeeded")
        persisted = next(call.args[0] for call in db.add.call_args_list if getattr(call.args[0], "signal_type", None) == "linguistic_analysis")
        self.assertEqual(persisted.value, result.value)
        return result

    def test_true_is_preserved_even_without_retained_observations(self):
        result = self.analyze({"has_psychosocial_content": True, "observations": []})
        self.assertIs(result.value["has_psychosocial_content"], True)
        self.assertEqual(result.observation_ids, [])

    def test_false_is_preserved_from_a_valid_extraction(self):
        result = self.analyze({"has_psychosocial_content": False, "observations": []})
        self.assertIs(result.value["has_psychosocial_content"], False)

    def test_invalid_or_missing_block_does_not_fabricate_false(self):
        for block in (None, {}, {"has_psychosocial_content": "false", "observations": []}):
            with self.subTest(block=block):
                result = self.analyze(block)
                self.assertEqual(result.psychosocial_status, "invalid_block")
                self.assertIsNone(result.value["has_psychosocial_content"])
                self.assertEqual(result.value["negative_valence"], 0.3)

    def test_skipped_short_text_does_not_claim_an_evaluated_answer(self):
        result = self.analyze({"has_psychosocial_content": False, "observations": []}, text="Hola")
        self.assertEqual(result.psychosocial_status, "skipped_short_text")
        self.assertIsNone(result.value["has_psychosocial_content"])


if __name__ == "__main__":
    unittest.main()
