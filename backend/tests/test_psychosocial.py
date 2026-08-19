"""Tests for Agent 4 and the deterministic psychosocial index.

The properties that matter here are the safety ones: a model output can
only enter the index through a schema-validated, domain-coherent,
quote-grounded row; and the index alone can never page a clinician.
"""
import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.content.prompts import AGENT4_DOMAIN_CATEGORIES, AGENT4_TOOL_SCHEMA
from app.models import PsychosocialObservation
from app.services import psychosocial
from app.services.llm.base import ProviderMetadata, StructuredAnalysisResult


def _observation(
    *,
    domain="housing",
    category="housing_precarious",
    valence="risk",
    intensity=0.8,
    confidence=0.9,
    is_change=False,
    status="inferred",
    days_ago=1,
    quote="me he ido a casa de un colega",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        domain=domain,
        category=category,
        valence=valence,
        intensity=intensity,
        confidence=confidence,
        is_change=is_change,
        status=status,
        summary="Resumen de la observación.",
        evidence_quote=quote,
        observed_at=datetime.utcnow() - timedelta(days=days_ago),
    )


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a):
        return self

    def limit(self, _n):
        return self

    def offset(self, _n):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def count(self):
        return len(self._rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.committed = False

    def query(self, _model):
        return _Query(self.rows)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, _obj):
        return None


class SchemaIntegrityTests(unittest.TestCase):
    def test_every_category_belongs_to_exactly_one_domain(self):
        seen: dict[str, str] = {}
        for domain, categories in AGENT4_DOMAIN_CATEGORIES.items():
            for category in categories:
                self.assertNotIn(category, seen, f"{category} duplicated in {domain} and {seen.get(category)}")
                seen[category] = domain

    def test_every_domain_has_a_weight_and_a_label(self):
        for domain in AGENT4_DOMAIN_CATEGORIES:
            self.assertIn(domain, psychosocial.DOMAIN_WEIGHTS, domain)
            self.assertIn(domain, psychosocial.DOMAIN_LABELS, domain)

    def test_every_category_has_a_spanish_label(self):
        for categories in AGENT4_DOMAIN_CATEGORIES.values():
            for category in categories:
                self.assertIn(category, psychosocial.CATEGORY_LABELS, category)

    def test_acute_change_categories_are_all_real_categories(self):
        known = set(psychosocial.CATEGORY_DOMAIN)
        self.assertEqual(psychosocial.ACUTE_CHANGE_CATEGORIES - known, set())

    def test_tool_schema_enums_match_the_catalog(self):
        properties = AGENT4_TOOL_SCHEMA["input_schema"]["properties"]["observations"]["items"]["properties"]
        self.assertEqual(set(properties["domain"]["enum"]), set(AGENT4_DOMAIN_CATEGORIES))
        self.assertEqual(set(properties["category"]["enum"]), set(psychosocial.CATEGORY_DOMAIN))


class ValidationTests(unittest.TestCase):
    def test_category_outside_its_domain_is_rejected(self):
        item = psychosocial.PsychosocialObservationIn(
            domain="housing",
            category="debt",  # belongs to `economic`
            valence="risk",
            intensity=0.5,
            confidence=0.5,
            is_change=False,
            summary="x",
            quote="y",
        )
        self.assertFalse(psychosocial._coherent(item))

    def test_matching_domain_and_category_is_accepted(self):
        item = psychosocial.PsychosocialObservationIn(
            domain="economic",
            category="debt",
            valence="risk",
            intensity=0.5,
            confidence=0.5,
            is_change=False,
            summary="x",
            quote="y",
        )
        self.assertTrue(psychosocial._coherent(item))

    def test_quote_must_appear_in_the_source_text(self):
        source = "Nada, que me he ido unos días a casa de un colega."
        self.assertTrue(psychosocial._quote_is_grounded("me he ido unos días a casa de un colega", source))
        self.assertTrue(psychosocial._quote_is_grounded("ME HE   IDO unos días", source))
        self.assertFalse(psychosocial._quote_is_grounded("me han desahuciado", source))

    def test_trivially_short_quotes_are_rejected(self):
        self.assertFalse(psychosocial._quote_is_grounded("de", "de casa"))


class ExtractionTests(unittest.TestCase):
    def _provider(self, value):
        return SimpleNamespace(
            analyze_structured=lambda *_a, **_k: StructuredAnalysisResult(
                value=value,
                metadata=ProviderMetadata(provider="anthropic", requested_model="stub"),
            )
        )

    def _run(self, value, text="Me he ido unos días a casa de un colega y he dejado el gimnasio."):
        db = _Db([])
        trace = SimpleNamespace(id=uuid.uuid4(), status="started")
        with patch.object(psychosocial.agent2_trace, "start", return_value=trace), patch.object(
            psychosocial.agent2_trace, "mark_succeeded", lambda *_a, **_k: None
        ), patch.object(psychosocial, "get_llm_provider", return_value=self._provider(value)):
            outcome = psychosocial.extract_and_store(
                db,
                uuid.uuid4(),
                text,
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        stored = [row for row in db.added if isinstance(row, PsychosocialObservation)]
        return outcome, stored

    def test_valid_observation_is_stored_as_inferred(self):
        outcome, stored = self._run(
            {
                "has_psychosocial_content": True,
                "observations": [
                    {
                        "domain": "housing",
                        "category": "housing_temporary",
                        "valence": "risk",
                        "intensity": 0.6,
                        "confidence": 0.8,
                        "is_change": True,
                        "summary": "Alojamiento temporal en casa de un conocido.",
                        "quote": "Me he ido unos días a casa de un colega",
                    }
                ],
            }
        )
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].status, "inferred")
        self.assertTrue(stored[0].is_change)

    def test_incoherent_domain_category_pair_is_dropped(self):
        _outcome, stored = self._run(
            {
                "has_psychosocial_content": True,
                "observations": [
                    {
                        "domain": "housing",
                        "category": "debt",
                        "valence": "risk",
                        "intensity": 0.6,
                        "confidence": 0.8,
                        "is_change": False,
                        "summary": "x",
                        "quote": "Me he ido unos días a casa de un colega",
                    }
                ],
            }
        )
        self.assertEqual(stored, [])

    def test_ungrounded_quote_is_dropped(self):
        """A fabricated citation must never reach the therapist's screen."""
        _outcome, stored = self._run(
            {
                "has_psychosocial_content": True,
                "observations": [
                    {
                        "domain": "economic",
                        "category": "benefit_loss",
                        "valence": "risk",
                        "intensity": 0.9,
                        "confidence": 0.9,
                        "is_change": True,
                        "summary": "Le han retirado la ayuda.",
                        "quote": "me han quitado la prestación",
                    }
                ],
            }
        )
        self.assertEqual(stored, [])

    def test_invalid_output_marks_the_trace_and_stores_nothing(self):
        db = _Db([])
        trace = SimpleNamespace(id=uuid.uuid4(), status="invalid_output")
        provider = self._provider({"has_psychosocial_content": "yes", "observations": []})
        with patch.object(psychosocial.agent2_trace, "start", return_value=trace), patch.object(
            psychosocial.agent2_trace, "mark_failed"
        ) as failed, patch.object(psychosocial, "get_llm_provider", return_value=provider):
            outcome = psychosocial.extract_and_store(
                db,
                uuid.uuid4(),
                "Me he ido a casa de un colega esta semana.",
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        failed.assert_called_once()
        self.assertEqual(db.added, [])
        self.assertEqual(outcome.observation_ids, [])

    def test_trace_failure_skips_the_outbound_call_entirely(self):
        db = _Db([])
        with patch.object(
            psychosocial.agent2_trace,
            "start",
            side_effect=psychosocial.agent2_trace.TracePersistenceError("nope"),
        ), patch.object(psychosocial, "get_llm_provider") as provider:
            outcome = psychosocial.extract_and_store(
                db,
                uuid.uuid4(),
                "Me he ido a casa de un colega esta semana.",
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        provider.assert_not_called()
        self.assertEqual(outcome.status, "trace_persistence_error")
        self.assertIsNone(outcome.trace_id)


class IndexTests(unittest.TestCase):
    def test_no_observations_yields_no_index(self):
        result = psychosocial.assess(_Db([]), uuid.uuid4())
        self.assertIsNone(result.index)
        self.assertEqual(result.band, "sin_datos")
        self.assertFalse(result.has_acute_change)

    def test_adverse_domains_raise_the_index(self):
        result = psychosocial.assess(
            _Db(
                [
                    _observation(domain="housing", category="housing_homeless", intensity=1.0, confidence=1.0),
                    _observation(
                        domain="social_support", category="support_absent", intensity=1.0, confidence=1.0
                    ),
                ]
            ),
            uuid.uuid4(),
        )
        self.assertEqual(result.index, 1.0)
        self.assertEqual(result.band, "alta")

    def test_protective_factors_offset_but_never_cancel(self):
        result = psychosocial.assess(
            _Db(
                [
                    _observation(domain="housing", category="housing_homeless", intensity=1.0, confidence=1.0),
                    _observation(
                        domain="social_support",
                        category="support_strong",
                        valence="protective",
                        intensity=1.0,
                        confidence=1.0,
                    ),
                ]
            ),
            uuid.uuid4(),
        )
        self.assertGreater(result.index, 0.0)
        self.assertLess(result.index, 1.0)

    def test_only_the_latest_observation_per_domain_counts(self):
        """A situation that improved must not keep being scored on its old state."""
        result = psychosocial.assess(
            _Db(
                [
                    _observation(category="housing_stable", valence="protective", days_ago=1),
                    _observation(category="housing_homeless", intensity=1.0, days_ago=40),
                ]
            ),
            uuid.uuid4(),
        )
        self.assertEqual(len(result.domains), 1)
        self.assertEqual(result.domains[0].category, "housing_stable")

    def test_refuted_observations_are_excluded_entirely(self):
        result = psychosocial.assess(
            _Db([_observation(status="refuted", intensity=1.0, confidence=1.0)]), uuid.uuid4()
        )
        self.assertIsNone(result.index)
        self.assertEqual(result.refuted_count, 1)

    def test_confirmed_observations_count_at_full_weight(self):
        low_confidence = psychosocial.assess(
            _Db([_observation(intensity=1.0, confidence=0.2)]), uuid.uuid4()
        )
        confirmed = psychosocial.assess(
            _Db([_observation(intensity=1.0, confidence=0.2, status="confirmed")]), uuid.uuid4()
        )
        self.assertGreater(confirmed.index, low_confidence.index)
        self.assertEqual(confirmed.index, 1.0)

    def test_recent_adverse_change_is_flagged_as_acute(self):
        result = psychosocial.assess(
            _Db([_observation(category="housing_temporary", is_change=True, days_ago=2)]),
            uuid.uuid4(),
        )
        self.assertTrue(result.has_acute_change)
        self.assertEqual(result.acute_changes[0].category, "housing_temporary")

    def test_an_old_change_is_no_longer_acute(self):
        result = psychosocial.assess(
            _Db([_observation(category="housing_temporary", is_change=True, days_ago=30)]),
            uuid.uuid4(),
        )
        self.assertFalse(result.has_acute_change)

    def test_a_protective_change_is_never_acute(self):
        result = psychosocial.assess(
            _Db(
                [
                    _observation(
                        domain="social_support",
                        category="new_supportive_relationship",
                        valence="protective",
                        is_change=True,
                        days_ago=1,
                    )
                ]
            ),
            uuid.uuid4(),
        )
        self.assertFalse(result.has_acute_change)

    def test_a_low_confidence_change_is_not_acute(self):
        result = psychosocial.assess(
            _Db([_observation(category="job_loss", domain="occupation", is_change=True, confidence=0.2)]),
            uuid.uuid4(),
        )
        self.assertFalse(result.has_acute_change)

    def test_snapshot_is_json_serialisable(self):
        import json

        result = psychosocial.assess(_Db([_observation()]), uuid.uuid4())
        json.dumps(result.as_dict())


class AdjudicationTests(unittest.TestCase):
    def test_confirming_records_the_actor_and_time(self):
        db = _Db([])
        row = _observation()
        actor = uuid.uuid4()
        psychosocial.adjudicate(db, row, status="confirmed", actor_id=actor, note="lo habló en sesión")
        self.assertEqual(row.status, "confirmed")
        self.assertEqual(row.adjudicated_by, actor)
        self.assertIsNotNone(row.adjudicated_at)
        self.assertEqual(row.adjudication_note, "lo habló en sesión")

    def test_unknown_status_is_rejected(self):
        with self.assertRaises(ValueError):
            psychosocial.adjudicate(_Db([]), _observation(), status="maybe", actor_id=uuid.uuid4())


if __name__ == "__main__":
    unittest.main()


class AcuteOrderingTests(unittest.TestCase):
    def test_simultaneous_changes_are_ordered_by_clinical_weight(self):
        """One message often yields several changes at the same instant.

        The panel and the alert lead with the first of them, so ordering by
        weight rather than by list position is what decides which sentence a
        therapist reads first.
        """
        same_moment = 1
        result = psychosocial.assess(
            _Db(
                [
                    _observation(
                        domain="connectedness",
                        category="loss_of_routine",
                        is_change=True,
                        intensity=0.6,
                        confidence=0.8,
                        days_ago=same_moment,
                    ),
                    _observation(
                        domain="housing",
                        category="housing_temporary",
                        is_change=True,
                        intensity=0.7,
                        confidence=0.9,
                        days_ago=same_moment,
                    ),
                ]
            ),
            uuid.uuid4(),
        )
        # housing weight 1.00 * 0.9 * 0.7 = 0.63 beats connectedness 0.85 * 0.8 * 0.6 = 0.408
        self.assertEqual(
            [state.category for state in result.acute_changes],
            ["housing_temporary", "loss_of_routine"],
        )
