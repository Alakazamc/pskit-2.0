import base64
import binascii
import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

password_hasher = PasswordHasher()
PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    return "argon2$" + password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("argon2$"):
        try:
            return password_hasher.verify(password_hash.removeprefix("argon2$"), password)
        except VerifyMismatchError:
            return False

    if password_hash.startswith("pbkdf2_sha256$"):
        try:
            _scheme, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
            return hmac.compare_digest(actual, expected)
        except (ValueError, TypeError, binascii.Error):
            return False
    return False


def password_backend_status() -> dict:
    return {"name": "Password hashing", "status": "ok", "detail": "argon2-cffi available"}
