"""Local SQLite/API regressions for aggregate scope, lineage and serialization."""
import json
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models import (
    Agent2AnalysisTrace, AlfaSignal, Baseline, ChatMessage, CheckIn,
    ConfirmedFact, DiaryEntry, PatientProfessionalAssignment, ProfessionalAlert,
    PsychosocialObservation, RiskAssessment, User,
)
from app.routers import professional, timeline as timeline_router
from app.schemas import DailyStatisticsOut, PatientMetricsOut, PatientTimelineOut, TimelineOut
from app.security import create_access_token
from app.services import clinical_view, daily_statistics, timeline

NOW = datetime(2026, 8, 31, 18)
SOURCE_AT = datetime(2026, 8, 29, 22, 30)  # Already Aug 30 in Madrid.


class _Clock(datetime):
    @classmethod
    def utcnow(cls):
        return NOW


class DailyStatisticsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        for model in (User, PatientProfessionalAssignment, CheckIn, Baseline, AlfaSignal, Agent2AnalysisTrace, ChatMessage, DiaryEntry, PsychosocialObservation, RiskAssessment, ProfessionalAlert, ConfirmedFact):
            model.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db: Session = self.sessions()
        self.patient = self.user("patient")
        self.other_patient = self.user("patient")
        self.therapist = self.user("therapist")
        self.admin = self.user("admin_clinical")
        self.db.add(PatientProfessionalAssignment(patient_id=self.patient.id, professional_id=self.therapist.id, status="active"))
        self.db.add_all([
            CheckIn(user_id=self.patient.id, mood=0, craving=0, sleep_hours=4, self_efficacy=2, created_at=SOURCE_AT),
            CheckIn(user_id=self.patient.id, mood=10, craving=2, sleep_hours=8, self_efficacy=8, created_at=SOURCE_AT + timedelta(minutes=15)),
            CheckIn(user_id=self.other_patient.id, mood=9, craving=9, sleep_hours=9, self_efficacy=9, created_at=SOURCE_AT),
        ])
        self.own_source = self.message(self.patient)
        self.other_source = self.message(self.other_patient)
        self.signal(self.patient, source=self.own_source, value={"negative_valence": 0.2, "ideation_direct": False, "ideation_indirect": False})
        self.signal(self.other_patient, source=self.other_source, value={"negative_valence": 1.0, "ideation_direct": True})
        self.db.commit()

        app = FastAPI()
        app.include_router(professional.router)
        app.include_router(timeline_router.router)

        def local_db():
            with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = local_db
        self.client = TestClient(app)
        self.metrics_clock = patch.object(clinical_view, "datetime", _Clock)
        self.timeline_clock = patch.object(timeline, "datetime", _Clock)
        self.metrics_clock.start()
        self.timeline_clock.start()

    def tearDown(self):
        self.metrics_clock.stop()
        self.timeline_clock.stop()
        self.client.close()
        self.db.close()
        self.engine.dispose()

    def user(self, role):
        user = User(email=f"{uuid.uuid4()}@example.test", display_name="Synthetic", hashed_password="not-used", role=role)
        self.db.add(user)
        self.db.commit()
        return user

    def message(self, patient, *, role="user"):
        message = ChatMessage(user_id=patient.id, role=role, content="SYNTHETIC_PRIVATE_TEXT", created_at=SOURCE_AT)
        self.db.add(message)
        self.db.commit()
        return message

    def signal(self, patient, *, source, value, trace_patient=None, active=True):
        trace = Agent2AnalysisTrace(
            correlation_id=uuid.uuid4(), user_id=(trace_patient or patient).id,
            source_type="chat_message", chat_message_id=source.id, status="succeeded",
            agent_role="analyzer_merged", provider="synthetic", requested_model="synthetic",
            effort="test", max_tokens=1, prompt_version="test", prompt_sha256="0" * 64,
            schema_version="test", schema_sha256="0" * 64,
            started_at=NOW.replace(tzinfo=timezone.utc) - timedelta(hours=1),
            created_at=NOW.replace(tzinfo=timezone.utc) - timedelta(hours=1),
        )
        self.db.add(trace)
        self.db.flush()
        signal = AlfaSignal(user_id=patient.id, agent2_trace_id=trace.id, signal_type="linguistic_analysis", value=value, is_active=active, timestamp=NOW - timedelta(minutes=30))
        self.db.add(signal)
        self.db.commit()
        return signal

    def headers(self, user):
        return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}

    def test_loader_joins_original_source_date_and_isolates_patient(self):
        result = daily_statistics.load_daily_statistics(self.db, self.patient.id, 7, now=NOW)
        self.assertEqual(len(result["daily"]), 1)
        day = result["daily"][0]
        self.assertEqual(day["date"], "2026-08-30")
        self.assertEqual(day["mood"], 5)
        self.assertEqual(day["sleep_hours"], 6)
        self.assertEqual(day["interaction_valence_mean"], 0.2)
        self.assertEqual(day["counts"]["interactions"], 1)
        self.assertIs(day["ideation"], False)
        self.assertIsNone(day["has_psychosocial_content"])
        self.assertEqual(result["provenance"]["interaction_timestamp_fallbacks"], 0)
        encoded = DailyStatisticsOut(**result).model_dump_json()
        self.assertNotIn("SYNTHETIC_PRIVATE_TEXT", encoded)
        self.assertNotIn(str(self.other_patient.id), encoded)

    def test_corrupt_cross_patient_and_assistant_source_links_are_not_counted(self):
        self.signal(self.patient, source=self.other_source, value={"negative_valence": 0.9})
        self.signal(self.patient, source=self.own_source, trace_patient=self.other_patient, value={"negative_valence": 0.8})
        assistant_source = self.message(self.patient, role="assistant")
        self.signal(self.patient, source=assistant_source, value={"negative_valence": 0.7})
        result = daily_statistics.load_daily_statistics(self.db, self.patient.id, 7, now=NOW)
        self.assertEqual(result["summary"]["negative_valence"]["mean"], 0.2)
        self.assertEqual(result["daily"][0]["counts"]["interactions"], 1)
        self.assertEqual(result["provenance"]["excluded_unverified_source_signals"], 3)

    def test_psychosocial_cross_patient_source_cannot_enter_aggregate(self):
        for source, intensity in ((self.own_source, 0.2), (self.other_source, 1.0)):
            self.db.add(PsychosocialObservation(user_id=self.patient.id, source_type="chat_message", chat_message_id=source.id, domain="housing", category="unstable_housing", valence="risk", intensity=intensity, confidence=0.8, is_change=True, summary="SYNTHETIC_OBSERVATION", evidence_quote="SYNTHETIC_PRIVATE_TEXT", status="inferred", observed_at=SOURCE_AT))
        self.db.commit()
        result = daily_statistics.load_daily_statistics(self.db, self.patient.id, 7, now=NOW)
        self.assertEqual(result["summary"]["psychosocial_intensity_mean"]["mean"], 0.2)
        self.assertEqual(result["provenance"]["excluded_unverified_source_observations"], 1)

    def test_patient_timeline_only_returns_own_last_checkin_values_without_analysis(self):
        queries = []
        event.listen(self.engine, "before_cursor_execute", lambda _conn, _cursor, statement, _params, _context, _many: queries.append(statement))
        response = self.client.get(f"/api/v1/timeline?window_days=7&patient_id={self.other_patient.id}", headers=self.headers(self.patient))
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(set(payload), {"points", "window_days"})
        result = PatientTimelineOut.model_validate(payload).model_dump()
        self.assertEqual(result["points"], [{"date": "2026-08-30", "mood": 10, "craving": 2, "sleep_hours": 8, "self_efficacy": 8}])
        self.assertEqual(set(payload["points"][0]), {"date", "mood", "craving", "sleep_hours", "self_efficacy"})
        self.assertFalse(any(table in query for query in queries for table in ("alfa_signals", "baselines", "agent2_analysis_traces", "psychosocial_observations")))
        self.assertEqual(self.client.get("/api/v1/timeline").status_code, 401)

    def test_patient_last_checkin_ties_use_id_and_preserve_zero_answers(self):
        for identifier, value in ((uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"), 0), (uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"), 9)):
            self.db.add(CheckIn(id=identifier, user_id=self.patient.id, mood=value, craving=value, sleep_hours=value, self_efficacy=value, created_at=SOURCE_AT + timedelta(minutes=30)))
        self.db.add(CheckIn(user_id=self.patient.id, mood=8, craving=8, sleep_hours=8, self_efficacy=8, created_at=NOW + timedelta(hours=1)))
        self.db.add(AlfaSignal(user_id=self.patient.id, signal_type="structural_score", value={"score": 0.2}, confidence_band="unstable", timestamp=NOW - timedelta(hours=1)))
        self.db.commit()
        response = self.client.get("/api/v1/timeline?window_days=7", headers=self.headers(self.patient))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["points"], [{"date": "2026-08-30", "mood": 0, "craving": 0, "sleep_hours": 0, "self_efficacy": 0}])

    def test_professional_timeline_keeps_statistics_and_patient_cannot_access_it(self):
        url = f"/api/v1/professional/patients/{self.patient.id}/timeline?window_days=7"
        response = self.client.get(url, headers=self.headers(self.therapist))
        self.assertEqual(response.status_code, 200, response.text)
        result = TimelineOut.model_validate(response.json())
        self.assertEqual(result.points[0].mood, 5)
        self.assertEqual(result.daily_statistics.summary["negative_valence"]["mean"], 0.2)
        self.assertIn("baseline_available", response.json())
        self.assertIn("structural_score", response.json()["points"][0])
        self.assertEqual(self.client.get(url, headers=self.headers(self.patient)).status_code, 403)

    def test_professional_metrics_preserve_assignment_and_role_gates(self):
        own_url = f"/api/v1/professional/patients/{self.patient.id}/metrics?window_days=7"
        response = self.client.get(own_url, headers=self.headers(self.therapist))
        self.assertEqual(response.status_code, 200, response.text)
        metrics = PatientMetricsOut.model_validate(response.json())
        self.assertEqual(metrics.daily_statistics.summary["mood"]["mean"], 5)
        self.assertEqual(len(metrics.daily_statistics.variables), 24)
        other_url = f"/api/v1/professional/patients/{self.other_patient.id}/metrics?window_days=7"
        self.assertEqual(self.client.get(other_url, headers=self.headers(self.therapist)).status_code, 403)
        self.assertEqual(self.client.get(own_url, headers=self.headers(self.patient)).status_code, 403)
        self.assertEqual(self.client.get(own_url, headers=self.headers(self.admin)).status_code, 403)

    def test_legacy_and_new_structural_versions_survive_response_models(self):
        self.db.add_all([
            AlfaSignal(user_id=self.patient.id, signal_type="structural_score", value={"score": 0}, confidence_band="unstable", timestamp=SOURCE_AT),
            AlfaSignal(user_id=self.patient.id, signal_type="structural_score", value={"score": 0.6, "calculation_version": "structural-v2"}, confidence_band="stable", timestamp=SOURCE_AT + timedelta(minutes=20)),
        ])
        self.db.commit()
        metrics = PatientMetricsOut(**clinical_view.build_metrics(self.db, self.patient.id, 7))
        self.assertEqual([point["calculation_version"] for point in metrics.structural], ["structural-v1", "structural-v2"])
        timeline_result = TimelineOut(**timeline.build_timeline(self.db, self.patient.id, 7)).model_dump()
        self.assertEqual(timeline_result["points"][0]["structural_calculation_version"], "structural-v2")
        self.assertEqual(timeline_result["points"][0]["structural_score"], 0.6)
        json.dumps(timeline_result, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
