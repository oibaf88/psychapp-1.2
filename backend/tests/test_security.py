import unittest

from app.security import hash_password, verify_password


class SecurityTests(unittest.TestCase):
    def test_verify_password_correct(self):
        plain_password = "MySecurePassword123!"
        hashed = hash_password(plain_password)
        self.assertTrue(verify_password(plain_password, hashed))

    def test_verify_password_incorrect(self):
        plain_password = "MySecurePassword123!"
        hashed = hash_password(plain_password)
        self.assertFalse(verify_password("WrongPassword123!", hashed))

    def test_verify_password_invalid_hash(self):
        self.assertFalse(verify_password("MySecurePassword123!", "invalid_hash"))
        self.assertFalse(verify_password("MySecurePassword123!", ""))
        self.assertFalse(verify_password("MySecurePassword123!", "not_a_bcrypt_hash"))

    def test_verify_password_invalid_types(self):
        self.assertFalse(verify_password("MySecurePassword123!", None))
        self.assertFalse(verify_password(None, "invalid_hash"))
        self.assertFalse(verify_password(12345, "invalid_hash"))

    def test_verify_password_long_password_truncation(self):
        # Password longer than 72 bytes
        long_password = "a" * 100
        hashed = hash_password(long_password)
        self.assertTrue(verify_password(long_password, hashed))

        # Passwords sharing the first 72 bytes should evaluate to True due to 72-byte truncation
        shared_prefix_pass = "a" * 72 + "different_suffix"
        self.assertTrue(verify_password(shared_prefix_pass, hashed))

        # Password differing within the first 72 bytes should evaluate to False
        different_pass = "a" * 70 + "bb" + "a" * 28
        self.assertFalse(verify_password(different_pass, hashed))


if __name__ == "__main__":
    unittest.main()
