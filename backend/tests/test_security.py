import unittest
import uuid
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from jose import jwt

from app.config import get_settings
from app.models import User
from app.security import (
    _BCRYPT_MAX_BYTES,
    create_access_token,
    get_current_user,
    hash_password,
    require_roles,
    verify_password,
)

settings = get_settings()


class SecurityTests(unittest.TestCase):
    def test_hash_and_verify_password(self):
        pw = "SuperSecret123"
        hashed = hash_password(pw)
        self.assertTrue(verify_password(pw, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_hash_password_returns_bcrypt_string(self):
        password = "my_secure_password"
        hashed = hash_password(password)

        self.assertIsInstance(hashed, str)
        self.assertNotEqual(password, hashed)
        self.assertTrue(hashed.startswith(("$2a$", "$2b$")))

    def test_hash_password_handles_empty_string(self):
        hashed = hash_password("")
        self.assertTrue(verify_password("", hashed))
        self.assertFalse(verify_password("not_empty", hashed))

    def test_hash_password_handles_unicode_characters(self):
        password = "🔒contraseña_123!_€"
        hashed = hash_password(password)
        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("🔒contraseña_123!_$", hashed))

    @patch("app.security.bcrypt.hashpw")
    @patch("app.security.bcrypt.gensalt")
    def test_hash_password_passes_truncated_bytes_to_bcrypt(self, mock_gensalt, mock_hashpw):
        mock_gensalt.return_value = b"test-salt"
        mock_hashpw.return_value = b"test-hash"

        password = "x" * 100
        result = hash_password(password)

        mock_gensalt.assert_called_once_with()
        mock_hashpw.assert_called_once_with(
            password.encode("utf-8")[:_BCRYPT_MAX_BYTES],
            mock_gensalt.return_value,
        )
        self.assertEqual(result, "test-hash")

    def test_verify_password_invalid_hash_returns_false(self):
        self.assertFalse(verify_password("password", "invalid_hash_format"))

    def test_verify_password_invalid_hash_variants_return_false(self):
        invalid_hashes = ("", "not_a_bcrypt_hash", "1234567890" * 3)
        for invalid_hash in invalid_hashes:
            with self.subTest(invalid_hash=invalid_hash):
                self.assertFalse(verify_password("SecretPassword123", invalid_hash))

    def test_verify_password_uses_bcrypt_72_byte_prefix(self):
        shared_prefix = "A" * _BCRYPT_MAX_BYTES
        hashed = hash_password(shared_prefix + "BBB")
        self.assertTrue(verify_password(shared_prefix + "CCC", hashed))

    def test_get_current_user_valid_token(self):
        user_id = uuid.uuid4()
        token = create_access_token(user_id, "patient")

        mock_user = MagicMock(spec=User)
        mock_user.id = user_id
        mock_user.is_active = True
        mock_user.role = "patient"

        mock_db = MagicMock()
        mock_db.get.return_value = mock_user

        user = get_current_user(token=token, db=mock_db)
        self.assertEqual(user, mock_user)
        mock_db.get.assert_called_once_with(User, user_id)

    def test_get_current_user_jwt_error_malformed_token(self):
        mock_db = MagicMock()
        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token="not.a.valid.jwt.token", db=mock_db)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Could not validate credentials")
        mock_db.get.assert_not_called()

    def test_get_current_user_jwt_error_invalid_signature(self):
        user_id = uuid.uuid4()
        token = jwt.encode({"sub": str(user_id)}, "wrong_secret", algorithm=settings.jwt_algorithm)
        mock_db = MagicMock()

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=token, db=mock_db)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Could not validate credentials")
        mock_db.get.assert_not_called()

    def test_get_current_user_missing_sub_claim(self):
        token = jwt.encode({"role": "patient"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        mock_db = MagicMock()

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=token, db=mock_db)

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Could not validate credentials")
        mock_db.get.assert_not_called()

    def test_get_current_user_not_found_or_inactive(self):
        user_id = uuid.uuid4()
        token = create_access_token(user_id, "patient")

        # Case 1: User not found in DB
        mock_db = MagicMock()
        mock_db.get.return_value = None

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=token, db=mock_db)
        self.assertEqual(ctx.exception.status_code, 401)

        # Case 2: User found but inactive
        mock_user = MagicMock(spec=User)
        mock_user.is_active = False
        mock_db.get.return_value = mock_user

        with self.assertRaises(HTTPException) as ctx:
            get_current_user(token=token, db=mock_db)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_require_roles_permission(self):
        user_patient = MagicMock(spec=User, role="patient")
        user_therapist = MagicMock(spec=User, role="therapist")

        dep = require_roles("therapist", "admin_clinical")

        # Allowed role
        self.assertEqual(dep(user=user_therapist), user_therapist)

        # Denied role raises 403
        with self.assertRaises(HTTPException) as ctx:
            dep(user=user_patient)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Insufficient permissions")


if __name__ == "__main__":
    unittest.main()
