import unittest
import uuid
from unittest.mock import MagicMock

from fastapi import HTTPException, status
from jose import jwt

from app.config import get_settings
from app.models import User
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    require_patient,
    require_professional,
    require_roles,
    verify_password,
)

settings = get_settings()


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.user_id = uuid.uuid4()
        self.user = User(
            id=self.user_id,
            email="test@example.com",
            role="patient",
            is_active=True,
        )

    def test_get_current_user_valid_token(self):
        token = create_access_token(self.user_id, role="patient")
        self.db.get.return_value = self.user

        current_user = get_current_user(token=token, db=self.db)
        self.assertEqual(current_user, self.user)
        self.db.get.assert_called_once_with(User, self.user_id)

    def test_get_current_user_invalid_jwt_token_raises_401(self):
        invalid_token = "invalid.jwt.token"
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=invalid_token, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(ctx.exception.detail, "Could not validate credentials")
        self.assertEqual(ctx.exception.headers, {"WWW-Authenticate": "Bearer"})

    def test_get_current_user_tampered_jwt_signature_raises_401(self):
        token = create_access_token(self.user_id, role="patient")
        tampered_token = token[:-5] + "XXXXX"
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=tampered_token, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_current_user_missing_sub_claim_raises_401(self):
        payload = {"role": "patient"}
        token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_current_user_nonexistent_user_raises_401(self):
        token = create_access_token(self.user_id, role="patient")
        self.db.get.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_current_user_inactive_user_raises_401(self):
        token = create_access_token(self.user_id, role="patient")
        self.user.is_active = False
        self.db.get.return_value = self.user

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=token, db=self.db)

        self.assertEqual(ctx.exception.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_require_roles_success(self):
        dep = require_roles("patient", "admin_clinical")
        res = dep(user=self.user)
        self.assertEqual(res, self.user)

    def test_require_roles_forbidden(self):
        dep = require_roles("therapist")
        with self.assertRaises(HTTPException) as ctx:
            dep(user=self.user)

        self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ctx.exception.detail, "Insufficient permissions")

    def test_require_role_helpers(self):
        self.user.role = "patient"
        self.assertEqual(require_patient(user=self.user), self.user)

        self.user.role = "therapist"
        self.assertEqual(require_professional(user=self.user), self.user)

        self.user.role = "admin_clinical"
        self.assertEqual(require_admin(user=self.user), self.user)

    def test_password_hashing_and_verification(self):
        plain = "SuperSecretPassword123!"
        hashed = hash_password(plain)

        self.assertNotEqual(plain, hashed)
        self.assertTrue(verify_password(plain, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_verify_password_invalid_hash_value_error(self):
        self.assertFalse(verify_password("password", "invalid_hash_string"))


if __name__ == "__main__":
    unittest.main()
