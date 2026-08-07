"""
Idempotent demo/seed data so the app is immediately explorable and so the
deterministic (fully local, no API key needed) parts of the risk engine
and timeline can be smoke-tested right after `docker compose up`.

Demo accounts (password for all: `DemoPass123!`):
  - patient@demo.psychapp.example.com   (patient)
  - therapist@demo.psychapp.example.com (therapist)
  - supervisor@demo.psychapp.example.com (supervisor)
  - admin@demo.psychapp.example.com    (admin_clinical)

  (Uses example.com so EmailStr / email-validator accepts them; .local is rejected.)

Disable by setting SEED_DEMO_DATA=false in .env.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import CheckIn, ConfirmedFact, Consent, PatientProfessionalAssignment, SafetyPlan, User
from app.security import hash_password

DEMO_PASSWORD = "DemoPass123!"


def _get_or_create_user(db: Session, email: str, display_name: str, role: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user
    user = User(email=email, hashed_password=hash_password(DEMO_PASSWORD), display_name=display_name, role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(Consent(user_id=user.id, consent_type="data_processing", granted=True))
    if role == "patient":
        db.add(SafetyPlan(user_id=user.id))
    db.commit()
    return user


def seed_demo_data(db: Session) -> None:
    if db.query(User).filter(User.email == "patient@demo.psychapp.example.com").first():
        return  # already seeded

    patient = _get_or_create_user(db, "patient@demo.psychapp.example.com", "Paciente Demo", "patient")
    therapist = _get_or_create_user(db, "therapist@demo.psychapp.example.com", "Dra. Terapeuta Demo", "therapist")
    _get_or_create_user(db, "supervisor@demo.psychapp.example.com", "Supervisor Demo", "supervisor")
    _get_or_create_user(db, "admin@demo.psychapp.example.com", "Admin Clínico Demo", "admin_clinical")

    assignment = PatientProfessionalAssignment(patient_id=patient.id, professional_id=therapist.id, status="active")
    db.add(assignment)
    db.add(Consent(user_id=patient.id, consent_type="professional_sharing", granted=True))
    db.commit()

    # 21 days of plausible, stable-ish check-ins so a baseline + a visible
    # structural_score can be computed immediately (fully local, no LLM
    # required) -- lets you verify the risk engine and timeline without
    # an ANTHROPIC_API_KEY configured.
    random.seed(42)
    now = datetime.utcnow()
    for days_ago in range(21, 0, -1):
        created_at = now - timedelta(days=days_ago, hours=random.randint(0, 6))
        db.add(
            CheckIn(
                user_id=patient.id,
                mood=random.randint(5, 7),
                craving=random.randint(2, 4),
                sleep_hours=round(random.uniform(6.0, 7.5), 1),
                self_efficacy=random.randint(5, 7),
                notes=None,
                created_at=created_at,
            )
        )
    db.commit()

    # Non-critical confirmed fact so the therapist dossier shows the "muro de hechos"
    # without incorrectly elevating risk (category "other" does not trigger N3/N4).
    db.add(
        ConfirmedFact(
            user_id=patient.id,
            category="other",
            content="Hecho demo: el paciente confirma que ha contactado con su red de apoyo esta semana.",
            declared_by="user",
        )
    )
    db.commit()

    # Run risk engine once so the therapist sees a real assessment (expected ~0–1
    # with stable synthetic check-ins), not a hand-crafted misclassified alert.
    from app.services import risk_engine

    risk_engine.run_and_persist(db, patient.id)
