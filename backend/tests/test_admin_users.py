import inspect
import unittest
import uuid
from datetime import datetime

from fastapi.params import Depends

from app.models import AuditLog, Consent, SafetyPlan, User
from app.routers import admin_users
from app.security import require_admin, verify_password


class _FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        if self.model is User:
            return self.session.existing_user
        if self.model is SafetyPlan:
            return self.session.safety_plan
        return None

    def all(self):
        if self.model is User:
            return list(self.session.users.values())
        return []


class _FakeSession:
    def __init__(self, users=None, existing_user=None, safety_plan=None):
        self.users = {user.id: user for user in (users or []) if user.id is not None}
        self.existing_user = existing_user
        self.safety_plan = safety_plan
        self.added = []

    def query(self, model):
        return _FakeQuery(self, model)

    def get(self, model, key):
        if model is User:
            return self.users.get(key)
        return None

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, User) and obj.id is not None:
            self.users[obj.id] = obj
        if isinstance(obj, SafetyPlan):
            self.safety_plan = obj

    def commit(self):
        pass

    def refresh(self, obj):
        if isinstance(obj, User):
            if obj.id is None:
                obj.id = uuid.uuid4()
                self.users[obj.id] = obj
            if obj.locale is None:
                obj.locale = "es-ES"
            if obj.is_active is None:
                obj.is_active = True
            if obj.created_at is None:
                obj.created_at = datetime.utcnow()


def _user(role: str, *, email: str) -> User:
    return User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="unused",
        display_name=email.split("@")[0],
        role=role,
        locale="es-ES",
        is_active=True,
        created_at=datetime.utcnow(),
    )


class AdminUserProvisioningTest(unittest.TestCase):
    def test_every_endpoint_requires_admin_clinical(self):
        for endpoint in (
            admin_users.list_users,
            admin_users.provision_user,
            admin_users.change_user_role,
        ):
            dependency = inspect.signature(endpoint).parameters["admin"].default
            self.assertIsInstance(dependency, Depends)
            self.assertIs(dependency.dependency, require_admin)

    def test_provisions_professional_with_hashed_password_and_audit(self):
        admin = _user("admin_clinical", email="admin@example.com")
        db = _FakeSession(users=[admin])

        created = admin_users.provision_user(
            admin_users.AdminUserCreate(
                email="doctor@example.com",
                password="CorrectHorseBatteryStaple",
                display_name="Dra. Demo",
                role="therapist",
            ),
            db=db,
            admin=admin,
        )

        user = next(obj for obj in db.added if isinstance(obj, User))
        self.assertEqual(created.role, "therapist")
        self.assertEqual(user.role, "therapist")
        self.assertNotEqual(user.hashed_password, "CorrectHorseBatteryStaple")
        self.assertTrue(verify_password("CorrectHorseBatteryStaple", user.hashed_password))
        self.assertTrue(any(isinstance(obj, Consent) for obj in db.added))
        self.assertTrue(
            any(
                isinstance(obj, AuditLog) and obj.action == "professional_user_provisioned"
                for obj in db.added
            )
        )

    def test_promotes_existing_patient_without_deleting_patient_state(self):
        admin = _user("admin_clinical", email="admin@example.com")
        patient = _user("patient", email="patient@example.com")
        existing_plan = SafetyPlan(user_id=patient.id)
        db = _FakeSession(users=[admin, patient], safety_plan=existing_plan)

        changed = admin_users.change_user_role(
            patient.id,
            admin_users.AdminRoleUpdate(role="supervisor"),
            db=db,
            admin=admin,
        )

        self.assertEqual(changed.role, "supervisor")
        self.assertIs(db.safety_plan, existing_plan)
        self.assertTrue(
            any(
                isinstance(obj, AuditLog)
                and obj.action == "user_role_changed"
                and obj.extra == {"previous_role": "patient", "new_role": "supervisor"}
                for obj in db.added
            )
        )

    def test_demotion_to_patient_creates_missing_safety_plan(self):
        admin = _user("admin_clinical", email="admin@example.com")
        therapist = _user("therapist", email="therapist@example.com")
        db = _FakeSession(users=[admin, therapist])

        changed = admin_users.change_user_role(
            therapist.id,
            admin_users.AdminRoleUpdate(role="patient"),
            db=db,
            admin=admin,
        )

        self.assertEqual(changed.role, "patient")
        self.assertIsInstance(db.safety_plan, SafetyPlan)
        self.assertEqual(db.safety_plan.user_id, therapist.id)

    def test_admin_cannot_change_own_role(self):
        admin = _user("admin_clinical", email="admin@example.com")
        db = _FakeSession(users=[admin])

        with self.assertRaises(admin_users.HTTPException) as ctx:
            admin_users.change_user_role(
                admin.id,
                admin_users.AdminRoleUpdate(role="therapist"),
                db=db,
                admin=admin,
            )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(admin.role, "admin_clinical")


if __name__ == "__main__":
    unittest.main()
