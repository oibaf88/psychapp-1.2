"""Maintenance must be append-only, atomic, resumable and never send emails."""
import unittest
import os
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.maintenance import refresh_risk_v14 as maintenance
from app.models import AuditLog, ProfessionalAlert, RiskAssessment
from app.services import risk_engine
from app.services.daily_statistics import aggregate_daily_statistics


class RiskMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        for model in (RiskAssessment, ProfessionalAlert, AuditLog):
            model.__table__.create(self.engine)
        self.patient = uuid.uuid4()
        self.decision = risk_engine.RiskDecision(
            level=3, triggering_rules=["N3_senal_linguistica_ideacion_indirecta"],
            reason="Posible ideación; valoración pendiente", input_facts={},
            input_signals={"structural_score": .75, "structural_calculation_version": "structural-v2"},
            calculation_trace={"conclusion": {"level": 3}},
        )

    def tearDown(self):
        self.engine.dispose()

    def test_preview_rolls_back_even_internal_commits_and_never_dispatches(self):
        with self.engine.connect() as connection:
            outer = connection.begin()
            with Session(connection, join_transaction_mode="rollback_only") as db:
                with patch.object(risk_engine, "calculate_risk_level", return_value=self.decision), patch.object(risk_engine.notification_service, "dispatch_for_alert") as dispatch:
                    assessment = risk_engine.run_and_persist(db, self.patient, notify=False)
                    self.assertEqual(assessment.alert_level, 3)
                    self.assertIsNotNone(assessment.generated_alert_id)
                    self.assertFalse(assessment.calculation_trace["notifications_enabled"])
                    dispatch.assert_not_called()
                outer.rollback()
        with Session(self.engine) as db:
            self.assertEqual(db.query(RiskAssessment).count(), 0)
            self.assertEqual(db.query(ProfessionalAlert).count(), 0)

    def test_live_pipeline_still_dispatches_new_alerts_by_default(self):
        with Session(self.engine) as db:
            with patch.object(risk_engine, "calculate_risk_level", return_value=self.decision), patch.object(risk_engine.notification_service, "dispatch_for_alert") as dispatch:
                assessment = risk_engine.run_and_persist(db, self.patient)
                dispatch.assert_called_once()
                self.assertTrue(assessment.calculation_trace["notifications_enabled"])

    def test_apply_preserves_history_and_audit_marker_prevents_duplicate_refresh(self):
        with Session(self.engine) as db:
            historical = RiskAssessment(user_id=self.patient, alert_level=2, model_version="risk-engine-v1.3", input_signals={"structural_score": 0}, triggering_rules=["N2_historical"], input_facts={}, calculation_trace={}, assessment_reason="Historical evaluation")
            db.add(historical)
            db.commit()
            historical_id = historical.id
        stats = aggregate_daily_statistics([], [], [], window_days=90)
        with self.engine.connect() as connection:
            outer = connection.begin()
            with Session(connection, join_transaction_mode="rollback_only") as db:
                with patch.object(maintenance, "load_daily_statistics", return_value=stats), patch.object(risk_engine, "calculate_risk_level", return_value=self.decision), patch.object(risk_engine.notification_service, "dispatch_for_alert") as dispatch:
                    self.assertFalse(maintenance.refresh_patient(db, self.patient)["skipped"])
                    self.assertTrue(maintenance.refresh_patient(db, self.patient)["skipped"])
                    dispatch.assert_not_called()
                outer.commit()
        with Session(self.engine) as db:
            self.assertEqual(db.query(RiskAssessment).count(), 2)
            historical = db.get(RiskAssessment, historical_id)
            self.assertEqual(historical.alert_level, 2)
            self.assertEqual(historical.input_signals["structural_score"], 0)
            self.assertEqual(db.query(AuditLog).count(), 1)
            self.assertEqual(db.query(ProfessionalAlert).count(), 1)

    def test_startup_does_nothing_without_explicit_operator_switch(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(maintenance, "refresh_all") as refresh:
            maintenance.run_configured_startup_refresh()
            refresh.assert_not_called()

    def test_startup_apply_previews_first_and_never_applies_failed_preview(self):
        with patch.dict(os.environ, {"RISK_V14_MAINTENANCE": "apply"}), patch.object(maintenance, "refresh_all", side_effect=RuntimeError("preview failed")) as refresh:
            with self.assertRaises(RuntimeError):
                maintenance.run_configured_startup_refresh()
            refresh.assert_called_once_with()
        with patch.dict(os.environ, {"RISK_V14_MAINTENANCE": "apply"}), patch.object(maintenance, "refresh_all", return_value={}) as refresh:
            maintenance.run_configured_startup_refresh()
            self.assertEqual(refresh.call_args_list, [unittest.mock.call(), unittest.mock.call(apply=True)])
