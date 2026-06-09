import base64
import hashlib
import hmac
import secrets


try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError
except Exception:  # pragma: no cover - only used on minimal dev hosts.
    PasswordHasher = None  # type: ignore[assignment]
    VerifyMismatchError = Exception  # type: ignore[assignment]


password_hasher = PasswordHasher() if PasswordHasher else None
PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    if password_hasher:
        return "argon2$" + password_hasher.hash(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, password_hash: str) -> bool:
    if password_hash.startswith("argon2$"):
        if not password_hasher:
            return False
        try:
            return password_hasher.verify(password_hash.removeprefix("argon2$"), password)
        except VerifyMismatchError:
            return False

    if password_hash.startswith("pbkdf2_sha256$"):
        try:
            _scheme, iterations, salt_b64, digest_b64 = password_hash.split("$", 3)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            actual = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, int(iterations)
            )
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False
    return False


def password_backend_status() -> dict:
    if password_hasher:
        return {"name": "Password hashing", "status": "ok", "detail": "argon2-cffi available"}
    return {
        "name": "Password hashing",
        "status": "warn",
        "detail": "argon2-cffi missing; using PBKDF2 development fallback",
    }
