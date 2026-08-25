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
from app.models import AlfaSignal, PsychosocialObservation
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


class ObservationBuildingTests(unittest.TestCase):
    """The three things that make an extraction trustworthy.

    These used to sit behind a provider call of their own. The analyser is
    merged now, so the same checks run over a block handed in from the
    single call — the checks themselves are unchanged, and they are the
    reason a fabricated citation never reaches a therapist's screen.
    """

    SOURCE = "Me he ido unos días a casa de un colega y he dejado el gimnasio."

    def _build(self, block, text=None):
        return psychosocial.build_observation_rows(
            block,
            user_id=uuid.uuid4(),
            text=text if text is not None else self.SOURCE,
            source_type="chat_message",
            source_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trace_id=uuid.uuid4(),
        )

    def test_valid_observation_is_built_as_inferred(self):
        rows = self._build(
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
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "inferred")
        self.assertTrue(rows[0].is_change)

    def test_incoherent_domain_category_pair_is_dropped(self):
        rows = self._build(
            {
                "has_psychosocial_content": True,
                "observations": [
                    {
                        "domain": "housing",
                        "category": "debt",  # belongs to `economic`
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
        self.assertEqual(rows, [])

    def test_ungrounded_quote_is_dropped(self):
        """A fabricated citation must never reach the therapist's screen."""
        rows = self._build(
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
        self.assertEqual(rows, [])

    def test_an_invalid_block_raises_rather_than_building_junk(self):
        with self.assertRaises(Exception):
            self._build({"has_psychosocial_content": "yes", "observations": []})

    def test_the_rows_carry_the_trace_they_were_analysed_under(self):
        trace_id = uuid.uuid4()
        rows = psychosocial.build_observation_rows(
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
                        "summary": "Alojamiento temporal.",
                        "quote": "Me he ido unos días a casa de un colega",
                    }
                ],
            },
            user_id=uuid.uuid4(),
            text=self.SOURCE,
            source_type="chat_message",
            source_id=uuid.uuid4(),
            correlation_id=uuid.uuid4(),
            trace_id=trace_id,
        )
        self.assertEqual(rows[0].trace_id, trace_id)


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


class MergedAnalyzerTests(unittest.TestCase):
    """One call per text, and one half failing must not cost the other.

    The point of the merge is the call count; the point of these tests is
    that nothing safety-relevant was traded for it.
    """

    SOURCE = "Me he ido unos días a casa de un colega y he dejado el gimnasio."

    LINGUISTIC = {
        "rumination_score": 0.2,
        "negative_valence": 0.3,
        "urgency_level": 0.1,
        "ideation_indirect": False,
        "ideation_direct": False,
        "consumption_crisis": False,
        "ambivalence": 0.4,
        "emotional_complexity": "low",
        "short_rationale": "Texto descriptivo sin señales de alarma.",
    }

    OBSERVATION = {
        "domain": "housing",
        "category": "housing_temporary",
        "valence": "risk",
        "intensity": 0.6,
        "confidence": 0.8,
        "is_change": True,
        "summary": "Alojamiento temporal en casa de un conocido.",
        "quote": "Me he ido unos días a casa de un colega",
    }

    def _provider(self, value):
        calls = []

        def _analyze(*args, **kwargs):
            calls.append((args, kwargs))
            return StructuredAnalysisResult(
                value=value,
                metadata=ProviderMetadata(provider="anthropic", requested_model="stub"),
            )

        return SimpleNamespace(analyze_structured=_analyze), calls

    def _run(self, value, text=None):
        from app.services import conversation

        db = _Db([])
        db.refresh = lambda row: setattr(row, "id", getattr(row, "id", None) or uuid.uuid4())
        trace = SimpleNamespace(id=uuid.uuid4(), status="started", error_code=None)
        provider, calls = self._provider(value)
        with patch.object(
            conversation.agent2_trace, "start", return_value=trace
        ), patch.object(
            conversation.agent2_trace, "mark_succeeded", lambda *_a, **_k: None
        ), patch.object(
            conversation.agent2_trace, "mark_failed", lambda *_a, **_k: None
        ), patch.object(
            conversation, "get_llm_provider", return_value=provider
        ):
            outcome = conversation.analyze_text_and_store(
                db,
                uuid.uuid4(),
                text if text is not None else self.SOURCE,
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        return outcome, db, calls, trace

    def test_one_provider_call_produces_both_readings(self):
        """This is the merge: two calls over the same text became one."""
        outcome, db, calls, _trace = self._run(
            {"linguistic": self.LINGUISTIC, "psychosocial": {
                "has_psychosocial_content": True, "observations": [self.OBSERVATION]}}
        )
        self.assertEqual(len(calls), 1, "the analyser must be called exactly once per text")
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.psychosocial_status, "succeeded")
        signals = [r for r in db.added if isinstance(r, AlfaSignal)]
        observations = [r for r in db.added if isinstance(r, PsychosocialObservation)]
        self.assertEqual(len(signals), 1)
        self.assertEqual(len(observations), 1)

    def test_both_readings_share_one_trace(self):
        _outcome, db, _calls, trace = self._run(
            {"linguistic": self.LINGUISTIC, "psychosocial": {
                "has_psychosocial_content": True, "observations": [self.OBSERVATION]}}
        )
        signal = next(r for r in db.added if isinstance(r, AlfaSignal))
        observation = next(r for r in db.added if isinstance(r, PsychosocialObservation))
        self.assertEqual(signal.agent2_trace_id, trace.id)
        self.assertEqual(observation.trace_id, trace.id)

    def test_a_bad_psychosocial_block_does_not_cost_the_linguistic_signal(self):
        """Trading a safety-critical input for a contextual one is the failure to avoid."""
        outcome, db, _calls, trace = self._run(
            {"linguistic": self.LINGUISTIC, "psychosocial": {
                "has_psychosocial_content": "yes", "observations": []}}
        )
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.psychosocial_status, "invalid_block")
        self.assertEqual(len([r for r in db.added if isinstance(r, AlfaSignal)]), 1)
        self.assertEqual([r for r in db.added if isinstance(r, PsychosocialObservation)], [])
        # The trace says so, without claiming the call itself failed.
        self.assertEqual(trace.error_code, "psychosocial_block_invalid")

    def test_a_missing_psychosocial_block_is_survivable(self):
        outcome, db, _calls, _trace = self._run({"linguistic": self.LINGUISTIC})
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.psychosocial_status, "invalid_block")
        self.assertEqual(len([r for r in db.added if isinstance(r, AlfaSignal)]), 1)

    def test_a_bad_linguistic_block_fails_the_whole_analysis(self):
        """The linguistic half feeds the risk engine; a broken one is not partial."""
        outcome, db, _calls, _trace = self._run(
            {"linguistic": {"rumination_score": "mucho"}, "psychosocial": {
                "has_psychosocial_content": True, "observations": [self.OBSERVATION]}}
        )
        self.assertNotEqual(outcome.status, "succeeded")
        self.assertIsNone(outcome.signal_id)
        self.assertEqual(db.added, [])

    def test_a_very_short_text_skips_the_psychosocial_half_only(self):
        outcome, db, calls, _trace = self._run(
            {"linguistic": self.LINGUISTIC, "psychosocial": {
                "has_psychosocial_content": True, "observations": [self.OBSERVATION]}},
            text="ok",
        )
        # The call still happens — the linguistic read is wanted either way.
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome.psychosocial_status, "skipped_short_text")
        self.assertEqual([r for r in db.added if isinstance(r, PsychosocialObservation)], [])

    def test_a_trace_that_will_not_commit_skips_the_outbound_call(self):
        from app.services import conversation

        db = _Db([])
        with patch.object(
            conversation.agent2_trace,
            "start",
            side_effect=conversation.agent2_trace.TracePersistenceError("nope"),
        ), patch.object(conversation, "get_llm_provider") as provider:
            outcome = conversation.analyze_text_and_store(
                db,
                uuid.uuid4(),
                self.SOURCE,
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        provider.assert_not_called()
        self.assertEqual(outcome.status, "trace_persistence_error")
        self.assertIsNone(outcome.trace_id)

    def test_new_traces_use_the_merged_role(self):
        from app.services import agent2_trace, conversation

        db = _Db([])
        db.refresh = lambda row: None
        seen = {}

        def _start(*_a, **kwargs):
            seen.update(kwargs)
            return SimpleNamespace(id=uuid.uuid4(), status="started", error_code=None)

        provider, _calls = self._provider(
            {"linguistic": self.LINGUISTIC, "psychosocial": {
                "has_psychosocial_content": False, "observations": []}}
        )
        with patch.object(conversation.agent2_trace, "start", _start), patch.object(
            conversation.agent2_trace, "mark_succeeded", lambda *_a, **_k: None
        ), patch.object(conversation, "get_llm_provider", return_value=provider):
            conversation.analyze_text_and_store(
                db,
                uuid.uuid4(),
                self.SOURCE,
                source_type="chat_message",
                source_id=uuid.uuid4(),
                correlation_id=uuid.uuid4(),
            )
        self.assertEqual(seen["agent_role"], agent2_trace.ANALYZER_ROLE)

    def test_the_retired_roles_still_resolve(self):
        """Traces already in the database must keep naming a known contract."""
        from app.services import agent2_trace

        self.assertIn("agent2_linguistic", agent2_trace.AGENT_CONTRACTS)
        self.assertIn("agent4_psychosocial", agent2_trace.AGENT_CONTRACTS)

    def test_the_merged_schema_reuses_both_original_schemas(self):
        """Merging must not quietly become a rewrite of either contract."""
        from app.content.prompts import (
            AGENT2_TOOL_SCHEMA,
            AGENT4_TOOL_SCHEMA,
            ANALYZER_TOOL_SCHEMA,
        )

        blocks = ANALYZER_TOOL_SCHEMA["input_schema"]["properties"]

        # The psychosocial block is the original, unchanged.
        self.assertEqual(
            blocks["psychosocial"]["properties"], AGENT4_TOOL_SCHEMA["input_schema"]["properties"]
        )
        self.assertEqual(
            blocks["psychosocial"]["required"], AGENT4_TOOL_SCHEMA["input_schema"]["required"]
        )

        # The linguistic block is the original plus the two personal-comparison
        # fields, which only mean anything in a prompt carrying a baseline.
        # Every original field must survive byte-for-byte: the risk engine
        # reads them, so a silent redefinition here is a silent change there.
        original = AGENT2_TOOL_SCHEMA["input_schema"]["properties"]
        merged = blocks["linguistic"]["properties"]
        for field, spec in original.items():
            self.assertEqual(spec, merged[field], f"{field} was redefined by the merge")
        self.assertEqual(
            set(merged) - set(original),
            {"deviation_from_own_baseline", "is_typical_for_patient"},
        )
        self.assertEqual(
            blocks["linguistic"]["required"][: len(AGENT2_TOOL_SCHEMA["input_schema"]["required"])],
            AGENT2_TOOL_SCHEMA["input_schema"]["required"],
        )

    def test_the_retired_agent2_contract_is_not_mutated(self):
        """Its sha256 is stamped on traces already in the database."""
        from app.content.prompts import AGENT2_TOOL_SCHEMA

        self.assertNotIn(
            "deviation_from_own_baseline", AGENT2_TOOL_SCHEMA["input_schema"]["properties"]
        )
        self.assertNotIn(
            "is_typical_for_patient", AGENT2_TOOL_SCHEMA["input_schema"]["required"]
        )


class RoleConstraintTests(unittest.TestCase):
    """The merged role has to be writable, and the old ones still readable.

    The unit tests above run against fakes, which cheerfully accept any
    string. The database does not: `ck_agent2_trace_agent_role` listed only
    the two retired roles, so without the widening migration the very first
    merged analysis would be rejected at INSERT — after the provider call
    had already been made and paid for.
    """

    def _constraint_sql(self):
        from app.models import Agent2AnalysisTrace

        for constraint in Agent2AnalysisTrace.__table__.constraints:
            if getattr(constraint, "name", None) == "ck_agent2_trace_agent_role":
                return str(constraint.sqltext)
        self.fail("ck_agent2_trace_agent_role is missing from the ORM")

    def test_the_orm_constraint_accepts_the_merged_role(self):
        from app.services import agent2_trace

        self.assertIn(agent2_trace.ANALYZER_ROLE, self._constraint_sql())

    def test_the_orm_constraint_still_accepts_the_retired_roles(self):
        sql = self._constraint_sql()
        self.assertIn("agent2_linguistic", sql)
        self.assertIn("agent4_psychosocial", sql)

    def test_every_registered_role_is_writable(self):
        """A role the code can start a trace with, but the DB rejects, is a bug."""
        from app.services import agent2_trace

        sql = self._constraint_sql()
        for role in agent2_trace.AGENT_CONTRACTS:
            self.assertIn(role, sql, f"{role} is registered but the DB constraint rejects it")

    def test_a_migration_widens_the_constraint_to_match(self):
        import pathlib

        migrations = pathlib.Path(__file__).resolve().parents[2] / "supabase" / "migrations"
        sql = "\n".join(p.read_text(encoding="utf-8") for p in migrations.glob("*.sql"))
        self.assertIn("'analyzer_merged', 'agent2_linguistic', 'agent4_psychosocial'", sql)

    def test_the_therapist_lineage_views_include_the_merged_role(self):
        """Otherwise the Agent 2 panels go quietly empty the day this ships."""
        import pathlib

        from app.services import agent2_trace

        self.assertIn(agent2_trace.ANALYZER_ROLE, agent2_trace.LINGUISTIC_ROLES)
        self.assertIn("agent2_linguistic", agent2_trace.LINGUISTIC_ROLES)

        backend = pathlib.Path(__file__).resolve().parents[1] / "app"
        for name in ("services/clinical_view.py", "routers/professional.py"):
            source = (backend / name).read_text(encoding="utf-8")
            self.assertNotIn(
                'agent_role == "agent2_linguistic"',
                source,
                f"{name} still filters traces to the retired role only",
            )
