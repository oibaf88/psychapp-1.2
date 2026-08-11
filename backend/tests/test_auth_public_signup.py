import unittest
import uuid

from jose import jwt

from app.models import Consent, SafetyPlan, User
from app.routers import auth
from app.schemas import GoogleLoginRequest, UserCreate


class _EmptyQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return None


class _FakeSession:
    def __init__(self):
        self.added = []

    def query(self, _model):
        return _EmptyQuery()

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def refresh(self, obj):
        if isinstance(obj, User):
            obj.id = uuid.uuid4()
            obj.locale = "es-ES"
            obj.is_active = True


class PublicSignupRoleTest(unittest.TestCase):
    def test_password_signup_cannot_self_assign_elevated_role(self):
        db = _FakeSession()
        token = auth.register(
            UserCreate(
                email="attacker@example.com",
                password="CorrectHorseBatteryStaple",
                display_name="Attacker",
                role="admin_clinical",
            ),
            db=db,
        )

        created_user = next(obj for obj in db.added if isinstance(obj, User))
        self.assertEqual(created_user.role, "patient")
        self.assertEqual(token.user.role, "patient")
        self.assertEqual(jwt.get_unverified_claims(token.access_token)["role"], "patient")
        self.assertTrue(any(isinstance(obj, Consent) for obj in db.added))
        self.assertTrue(any(isinstance(obj, SafetyPlan) for obj in db.added))

    def test_password_signup_ignores_unknown_role_strings(self):
        db = _FakeSession()
        token = auth.register(
            UserCreate(
                email="unknown-role@example.com",
                password="CorrectHorseBatteryStaple",
                display_name="Unknown Role",
                role="owner",
            ),
            db=db,
        )

        created_user = next(obj for obj in db.added if isinstance(obj, User))
        self.assertEqual(created_user.role, "patient")
        self.assertEqual(token.user.role, "patient")

    def test_mock_google_signup_cannot_self_assign_elevated_role(self):
        db = _FakeSession()
        previous = auth.settings.allow_mock_google_login
        auth.settings.allow_mock_google_login = True
        try:
            token = auth.google_login(
                GoogleLoginRequest(id_token="doctor@example.com", role="therapist"),
                db=db,
            )
        finally:
            auth.settings.allow_mock_google_login = previous

        created_user = next(obj for obj in db.added if isinstance(obj, User))
        self.assertEqual(created_user.role, "patient")
        self.assertEqual(token.user.role, "patient")
        self.assertEqual(jwt.get_unverified_claims(token.access_token)["role"], "patient")


if __name__ == "__main__":
    unittest.main()
