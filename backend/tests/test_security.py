import unittest
from unittest.mock import patch
import bcrypt

from app.security import hash_password, verify_password, _BCRYPT_MAX_BYTES


class SecurityHashPasswordTest(unittest.TestCase):
    def test_hash_password_returns_hashed_string(self):
        password = "my_secure_password"
        hashed = hash_password(password)

        self.assertIsInstance(hashed, str)
        self.assertNotEqual(password, hashed)
        self.assertTrue(hashed.startswith("$2b$") or hashed.startswith("$2a$"))

    def test_hash_password_verifiable_with_verify_password(self):
        password = "correct_password"
        hashed = hash_password(password)

        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("wrong_password", hashed))

    def test_hash_password_truncates_at_max_bytes(self):
        # Passwords longer than 72 bytes should produce the same hash prefix / match
        # as passwords truncated at 72 bytes.
        base_72 = "a" * _BCRYPT_MAX_BYTES
        longer_80 = "a" * 80

        hashed_base = hash_password(base_72)
        # Verify that longer password verifies against the hash generated from base_72
        self.assertTrue(verify_password(longer_80, hashed_base))

    def test_hash_password_handles_empty_string(self):
        password = ""
        hashed = hash_password(password)

        self.assertIsInstance(hashed, str)
        self.assertTrue(verify_password("", hashed))
        self.assertFalse(verify_password("not_empty", hashed))

    def test_hash_password_handles_unicode_characters(self):
        password = "🔒contraseña_123!_€"
        hashed = hash_password(password)

        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("🔒contraseña_123!_$", hashed))

    @patch("bcrypt.hashpw")
    @patch("bcrypt.gensalt")
    def test_hash_password_calls_bcrypt_with_truncated_bytes(self, mock_gensalt, mock_hashpw):
        mock_gensalt.return_value = b"$2b$12$fakegensaltstringhere"
        mock_hashpw.return_value = b"$2b$12$fakehashedpasswordstring"

        long_password = "x" * 100
        result = hash_password(long_password)

        mock_gensalt.assert_called_once()
        expected_bytes = long_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        mock_hashpw.assert_called_once_with(expected_bytes, mock_gensalt.return_value)
        self.assertEqual(result, "$2b$12$fakehashedpasswordstring")


if __name__ == "__main__":
    unittest.main()
