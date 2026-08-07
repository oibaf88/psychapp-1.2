"""
Notification dispatcher. Simplified from doc 18's full push/SMS/email
worker+queue design (Redis Streams/RabbitMQ + FCM/APNs + Twilio +
SendGrid) down to what a locally-run, no-paid-accounts MVP can do:

  - in_app: always available, just a DB row the frontend polls / fetches.
  - email: only if SMTP_* env vars are configured; silently skipped
    otherwise (never blocks the request/alert flow).

SMS (Twilio/MessageBird) and mobile push (FCM/APNs) from doc 18 are
explicitly NOT implemented here -- they need paid third-party accounts
and device tokens that don't exist in a local dev/demo environment. See
README "Assumptions and gaps". The `Notification` table and
`channel` field are already modeled so a real SMS/push sender can be
added later without changing the data model.
"""
import smtplib
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from app.config import get_settings
from app.content.safety_resources import (
    LEVEL3_PROFESSIONAL_NOTIFICATION_TEMPLATE,
    LEVEL4_PROFESSIONAL_NOTIFICATION_TEMPLATE,
)
from app.models import Notification, PatientProfessionalAssignment, ProfessionalAlert, User

settings = get_settings()


def _send_email(to_address: str, subject: str, body: str) -> bool:
    if not settings.smtp_host:
        return False
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to_address
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, [to_address], msg.as_string())
        return True
    except Exception:  # noqa: BLE001 - notification failures must never break the request
        return False


def dispatch_for_alert(db: Session, alert: ProfessionalAlert) -> None:
    patient = db.get(User, alert.user_id)
    patient_label = patient.display_name if patient else str(alert.user_id)

    template = (
        LEVEL4_PROFESSIONAL_NOTIFICATION_TEMPLATE
        if alert.alert_level == 4
        else LEVEL3_PROFESSIONAL_NOTIFICATION_TEMPLATE
    )
    body = template.format(patient_label=patient_label)

    assignments = (
        db.query(PatientProfessionalAssignment)
        .filter(
            PatientProfessionalAssignment.patient_id == alert.user_id,
            PatientProfessionalAssignment.status == "active",
        )
        .all()
    )

    for assignment in assignments:
        professional = db.get(User, assignment.professional_id)
        if professional is None:
            continue

        notif = Notification(
            professional_id=professional.id,
            recipient_type="professional",
            channel="in_app",
            alert_level=alert.alert_level,
            template_code=f"level{alert.alert_level}_professional",
            title=alert.title,
            body=body,
            related_alert_id=alert.id,
            related_assessment_id=alert.related_assessment_id,
            status="sent",
        )
        db.add(notif)

        email_notif = Notification(
            professional_id=professional.id,
            recipient_type="professional",
            channel="email",
            alert_level=alert.alert_level,
            template_code=f"level{alert.alert_level}_professional_email",
            title=alert.title,
            body=body,
            related_alert_id=alert.id,
            related_assessment_id=alert.related_assessment_id,
        )
        sent = _send_email(professional.email, alert.title, body)
        email_notif.status = "sent" if sent else "failed"
        db.add(email_notif)

    # In-app "soft" notice to the patient (never mentions the numeric level)
    patient_notif = Notification(
        user_id=alert.user_id,
        recipient_type="patient",
        channel="in_app",
        alert_level=alert.alert_level,
        template_code=f"level{alert.alert_level}_patient",
        title="Actualización de tu acompañamiento",
        body=(
            "Tu profesional de referencia ha sido informado para que pueda acompañarte mejor."
            if assignments
            else "Hemos registrado esta situación. Revisa tu plan de seguridad si lo necesitas."
        ),
        related_alert_id=alert.id,
        status="sent",
    )
    db.add(patient_notif)
    db.commit()
