import unittest

from app.security import hash_password, verify_password


class SecurityTestCase(unittest.TestCase):
    def test_verify_password_success(self):
        password = "SecretPassword123"
        hashed = hash_password(password)
        self.assertTrue(verify_password(password, hashed))

    def test_verify_password_wrong_password(self):
        password = "SecretPassword123"
        hashed = hash_password(password)
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_verify_password_invalid_hash_raises_value_error_internally(self):
        invalid_hashes = [
            "invalid_hash",
            "not_a_bcrypt_hash",
            "1234567890" * 3,
            "",
        ]
        for invalid_hash in invalid_hashes:
            with self.subTest(invalid_hash=invalid_hash):
                self.assertFalse(verify_password("SecretPassword123", invalid_hash))

    def test_password_truncation_max_bytes(self):
        long_password_prefix = "A" * 72
        long_password_1 = long_password_prefix + "BBB"
        long_password_2 = long_password_prefix + "CCC"

        hashed = hash_password(long_password_1)
        # Because password is truncated to first 72 bytes, long_password_2 will match hashed long_password_1
        self.assertTrue(verify_password(long_password_2, hashed))


if __name__ == "__main__":
    unittest.main()
