import unittest
import uuid
from unittest.mock import MagicMock, patch

from app.models import Notification, PatientProfessionalAssignment, ProfessionalAlert, User
from app.services import notifications


class SendEmailTests(unittest.TestCase):
    """Tests for the private `_send_email` helper function."""

    def test_send_email_returns_false_when_smtp_host_not_configured(self):
        with patch.object(notifications.settings, "smtp_host", None):
            result = notifications._send_email("doctor@example.com", "Test Subject", "Test Body")
            self.assertFalse(result)

    @patch("smtplib.SMTP")
    def test_send_email_success_without_smtp_auth(self, mock_smtp):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        with patch.object(notifications.settings, "smtp_host", "smtp.example.com"), \
             patch.object(notifications.settings, "smtp_port", 587), \
             patch.object(notifications.settings, "smtp_from", "noreply@example.com"), \
             patch.object(notifications.settings, "smtp_user", None), \
             patch.object(notifications.settings, "smtp_password", None):

            result = notifications._send_email("doctor@example.com", "Alert Title", "Alert Body")

            self.assertTrue(result)
            mock_smtp.assert_called_once_with("smtp.example.com", 587, timeout=10)
            mock_server.starttls.assert_called_once()
            mock_server.login.assert_not_called()
            mock_server.sendmail.assert_called_once()
            args, _ = mock_server.sendmail.call_args
            self.assertEqual(args[0], "noreply@example.com")
            self.assertEqual(args[1], ["doctor@example.com"])
            self.assertIn("Subject: Alert Title", args[2])
            self.assertIn("Alert Body", args[2])

    @patch("smtplib.SMTP")
    def test_send_email_success_with_smtp_auth(self, mock_smtp):
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = mock_server

        with patch.object(notifications.settings, "smtp_host", "smtp.example.com"), \
             patch.object(notifications.settings, "smtp_port", 587), \
             patch.object(notifications.settings, "smtp_from", "noreply@example.com"), \
             patch.object(notifications.settings, "smtp_user", "smtp_user_val"), \
             patch.object(notifications.settings, "smtp_password", "smtp_pass_val"):

            result = notifications._send_email("doctor@example.com", "Alert Title", "Alert Body")

            self.assertTrue(result)
            mock_server.login.assert_called_once_with("smtp_user_val", "smtp_pass_val")
            mock_server.sendmail.assert_called_once()

    @patch("smtplib.SMTP")
    def test_send_email_catches_exception_and_returns_false(self, mock_smtp):
        mock_smtp.side_effect = Exception("SMTP connection failure")

        with patch.object(notifications.settings, "smtp_host", "smtp.example.com"), \
             patch.object(notifications.settings, "smtp_port", 587), \
             patch.object(notifications.settings, "smtp_from", "noreply@example.com"):

            result = notifications._send_email("doctor@example.com", "Alert Title", "Alert Body")

            self.assertFalse(result)


class FakeQuery:
    def __init__(self, items):
        self._items = items

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self._items


class DispatchForAlertTests(unittest.TestCase):
    """Tests for `dispatch_for_alert` function."""

    def setUp(self):
        self.patient_id = uuid.uuid4()
        self.professional_id = uuid.uuid4()
        self.alert_id = uuid.uuid4()
        self.assessment_id = uuid.uuid4()

        self.patient = User(
            id=self.patient_id,
            display_name="Jane Doe",
            email="jane@example.com",
            role="patient",
        )
        self.professional = User(
            id=self.professional_id,
            display_name="Dr. Smith",
            email="drsmith@example.com",
            role="professional",
        )

        self.alert_level_3 = ProfessionalAlert(
            id=self.alert_id,
            user_id=self.patient_id,
            alert_level=3,
            title="Level 3 Risk Alert",
            related_assessment_id=self.assessment_id,
        )

        self.alert_level_4 = ProfessionalAlert(
            id=self.alert_id,
            user_id=self.patient_id,
            alert_level=4,
            title="Level 4 High Risk Alert",
            related_assessment_id=self.assessment_id,
        )

        self.assignment = PatientProfessionalAssignment(
            id=uuid.uuid4(),
            patient_id=self.patient_id,
            professional_id=self.professional_id,
            status="active",
        )

    @patch("app.services.notifications._send_email")
    def test_dispatch_level_3_alert_with_active_assignment_and_successful_email(self, mock_send_email):
        mock_send_email.return_value = True

        added_objects = []

        def mock_get(model, obj_id):
            if model == User:
                if obj_id == self.patient_id:
                    return self.patient
                if obj_id == self.professional_id:
                    return self.professional
            return None

        mock_db = MagicMock()
        mock_db.get.side_effect = mock_get
        mock_db.query.return_value = FakeQuery([self.assignment])
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        notifications.dispatch_for_alert(mock_db, self.alert_level_3)

        mock_db.commit.assert_called_once()
        mock_send_email.assert_called_once_with(
            "drsmith@example.com",
            "Level 3 Risk Alert",
            notifications.LEVEL3_PROFESSIONAL_NOTIFICATION_TEMPLATE.format(patient_label="Jane Doe")
        )

        self.assertEqual(len(added_objects), 3)

        prof_inapp = [n for n in added_objects if n.recipient_type == "professional" and n.channel == "in_app"][0]
        self.assertEqual(prof_inapp.professional_id, self.professional_id)
        self.assertEqual(prof_inapp.alert_level, 3)
        self.assertEqual(prof_inapp.template_code, "level3_professional")
        self.assertEqual(prof_inapp.title, "Level 3 Risk Alert")
        self.assertEqual(prof_inapp.status, "sent")
        self.assertEqual(prof_inapp.related_alert_id, self.alert_id)
        self.assertEqual(prof_inapp.related_assessment_id, self.assessment_id)

        prof_email = [n for n in added_objects if n.recipient_type == "professional" and n.channel == "email"][0]
        self.assertEqual(prof_email.professional_id, self.professional_id)
        self.assertEqual(prof_email.alert_level, 3)
        self.assertEqual(prof_email.template_code, "level3_professional_email")
        self.assertEqual(prof_email.title, "Level 3 Risk Alert")
        self.assertEqual(prof_email.status, "sent")
        self.assertEqual(prof_email.related_alert_id, self.alert_id)

        patient_notif = [n for n in added_objects if n.recipient_type == "patient"][0]
        self.assertEqual(patient_notif.user_id, self.patient_id)
        self.assertEqual(patient_notif.channel, "in_app")
        self.assertEqual(patient_notif.alert_level, 3)
        self.assertEqual(patient_notif.template_code, "level3_patient")
        self.assertEqual(patient_notif.title, "Actualización de tu acompañamiento")
        self.assertIn("Tu profesional de referencia ha sido informado", patient_notif.body)
        self.assertEqual(patient_notif.status, "sent")

    @patch("app.services.notifications._send_email")
    def test_dispatch_level_4_alert_when_email_fails(self, mock_send_email):
        mock_send_email.return_value = False

        added_objects = []

        def mock_get(model, obj_id):
            if model == User:
                if obj_id == self.patient_id:
                    return self.patient
                if obj_id == self.professional_id:
                    return self.professional
            return None

        mock_db = MagicMock()
        mock_db.get.side_effect = mock_get
        mock_db.query.return_value = FakeQuery([self.assignment])
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        notifications.dispatch_for_alert(mock_db, self.alert_level_4)

        prof_email = [n for n in added_objects if n.recipient_type == "professional" and n.channel == "email"][0]
        self.assertEqual(prof_email.status, "failed")
        self.assertEqual(prof_email.template_code, "level4_professional_email")

    @patch("app.services.notifications._send_email")
    def test_dispatch_without_assignments_or_missing_patient(self, mock_send_email):
        added_objects = []

        mock_db = MagicMock()
        mock_db.get.return_value = None  # Patient not found in DB
        mock_db.query.return_value = FakeQuery([])  # No assignments
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        notifications.dispatch_for_alert(mock_db, self.alert_level_3)

        mock_send_email.assert_not_called()
        self.assertEqual(len(added_objects), 1)

        patient_notif = added_objects[0]
        self.assertEqual(patient_notif.recipient_type, "patient")
        self.assertIn("Hemos registrado esta situación. Revisa tu plan de seguridad", patient_notif.body)

    @patch("app.services.notifications._send_email")
    def test_dispatch_when_assigned_professional_not_found(self, mock_send_email):
        added_objects = []

        def mock_get(model, obj_id):
            if model == User and obj_id == self.patient_id:
                return self.patient
            return None  # Professional user missing

        mock_db = MagicMock()
        mock_db.get.side_effect = mock_get
        mock_db.query.return_value = FakeQuery([self.assignment])
        mock_db.add.side_effect = lambda obj: added_objects.append(obj)

        notifications.dispatch_for_alert(mock_db, self.alert_level_3)

        mock_send_email.assert_not_called()
        self.assertEqual(len(added_objects), 1)
        self.assertEqual(added_objects[0].recipient_type, "patient")


if __name__ == "__main__":
    unittest.main()
