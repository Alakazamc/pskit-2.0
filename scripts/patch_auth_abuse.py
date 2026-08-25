"""Install the authentication abuse guard into a copied backend image."""

from pathlib import Path
import sys


def patch_main(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if "AuthAbuseMiddleware" in source:
        return
    import_line = "from app.security_headers import SecurityHeadersMiddleware\n"
    if import_line not in source:
        raise SystemExit("refusing patch: security middleware import anchor not found")
    source = source.replace(
        import_line,
        import_line + "from app.middleware.auth_abuse import AuthAbuseMiddleware\n",
        1,
    )
    marker = "    app.add_middleware(SecurityHeadersMiddleware"
    if marker not in source:
        raise SystemExit("refusing patch: security middleware registration anchor not found")
    source = source.replace(
        marker,
        "    app.add_middleware(AuthAbuseMiddleware)\n" + marker,
        1,
    )
    path.write_text(source, encoding="utf-8")


def patch_sessions(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if "configured_expires_at = issued_at" in source:
        return
    anchor = "    expires_at = session.expires_at\n"
    if anchor not in source:
        raise SystemExit("refusing patch: session expiry anchor not found")
    replacement = anchor + (
        "    if expires_at.tzinfo is None:\n"
        "        expires_at = expires_at.replace(tzinfo=timezone.utc)\n"
        "    issued_at = session.created_at\n"
        "    if issued_at.tzinfo is None:\n"
        "        issued_at = issued_at.replace(tzinfo=timezone.utc)\n"
        "    configured_expires_at = issued_at + timedelta(days=get_settings().session_ttl_days)\n"
        "    if configured_expires_at < expires_at:\n"
        "        expires_at = configured_expires_at\n"
    )
    path.write_text(source.replace(anchor, replacement, 1), encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_auth_abuse.py BACKEND_ROOT")
    backend_root = Path(sys.argv[1])
    patch_main(backend_root / "app" / "main.py")
    patch_sessions(backend_root / "app" / "auth" / "sessions.py")


if __name__ == "__main__":
    main()
