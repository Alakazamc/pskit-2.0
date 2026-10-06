"""Create a fresh, private staging configuration without reading production secrets."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "infra/supabase/.env.example"
KEY_SCRIPT = Path(__file__).with_name("staging_auth_keys.mjs")


def _secret(length: int = 32) -> str:
    return secrets.token_hex(length)


def _image_id(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True, text=True, check=True,
    )
    value = result.stdout.strip()
    if not value.startswith("sha256:"):
        raise ValueError("Backend image is not available as a fixed image ID")
    return value


def _auth_keys(jwt_secret: str, image: str) -> dict[str, str]:
    local_node = os.environ.get("STAGING_AUTH_KEYS_NODE")
    if local_node:
        command = [local_node, str(KEY_SCRIPT)]
    else:
        command = [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-i", "-v", f"{KEY_SCRIPT}:/staging_auth_keys.mjs:ro",
            "--entrypoint", "node", image, "/staging_auth_keys.mjs",
        ]
    try:
        result = subprocess.run(
            command, input=json.dumps({"jwtSecret": jwt_secret}),
            capture_output=True, text=True, check=True,
        )
        values = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError("Staging Auth key generation failed") from exc
    required = {"ANON_KEY", "SERVICE_ROLE_KEY", "SUPABASE_PUBLISHABLE_KEY",
                "SUPABASE_SECRET_KEY", "ANON_KEY_ASYMMETRIC",
                "SERVICE_ROLE_KEY_ASYMMETRIC", "JWT_KEYS", "JWT_JWKS"}
    if not required <= values.keys():
        raise RuntimeError("Staging Auth key generation returned incomplete data")
    return values


def _render_template(values: dict[str, str]) -> str:
    lines = []
    seen = set()
    for line in TEMPLATE.read_text().splitlines():
        key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
        if key in values:
            lines.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    return "\n".join(lines) + "\n"


def _private_write(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)


def _publish_directory(source: Path, target: Path) -> None:
    """Linux renameat2 prevents even an empty pre-existing directory being replaced."""
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(
        -100, os.fsencode(source), -100, os.fsencode(target), 1,
    )
    if result != 0:
        code = ctypes.get_errno()
        if code == 17:
            raise FileExistsError(target)
        raise OSError(code, os.strerror(code), target)


def _dist_hash(dist: Path) -> str:
    digest = hashlib.sha256()
    contents = sorted(dist.rglob("*"))
    if any(path.is_symlink() for path in contents):
        raise ValueError("Frontend dist must not contain a symlink")
    for path in (p for p in contents if p.is_file()):
        digest.update(str(path.relative_to(dist)).encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def prepare_staging(target_dir: Path, *, backend_image: str, frontend_dist: Path) -> None:
    target = Path(target_dir).absolute()
    dist = Path(frontend_dist).resolve(strict=True)
    if not (dist / "index.html").is_file():
        raise ValueError("Frontend dist lacks index.html")
    if target.is_symlink() or target.exists():
        raise FileExistsError(target)
    if target == ROOT or ROOT in target.parents:
        raise ValueError("Private staging configuration must be outside the repository")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    image_id = _image_id(backend_image)
    temp = Path(tempfile.mkdtemp(prefix=".staging-", dir=target.parent))
    try:
        jwt_secret = _secret()
        auth = _auth_keys(jwt_secret, backend_image)
        postgres_password = _secret(24)
        litellm_db_password = _secret(24)
        pskit_db_password = _secret(24)
        supabase = {
            "POSTGRES_PASSWORD": postgres_password,
            "JWT_SECRET": jwt_secret,
            **auth,
            "DASHBOARD_PASSWORD": _secret(24),
            "SECRET_KEY_BASE": _secret(48),
            "REALTIME_DB_ENC_KEY": _secret(8),
            "VAULT_ENC_KEY": _secret(16),
            "PG_META_CRYPTO_KEY": _secret(24),
            "LOGFLARE_PUBLIC_ACCESS_TOKEN": _secret(24),
            "LOGFLARE_PRIVATE_ACCESS_TOKEN": _secret(24),
            "S3_PROTOCOL_ACCESS_KEY_ID": _secret(16),
            "S3_PROTOCOL_ACCESS_KEY_SECRET": _secret(32),
            "MINIO_ROOT_PASSWORD": _secret(16),
            "POOLER_TENANT_ID": _secret(12),
            "SITE_URL": "http://10.9.8.1:18132",
            "SUPABASE_PUBLIC_URL": "http://10.9.8.1:18132",
            "API_EXTERNAL_URL": "http://10.9.8.1:18132/auth/v1",
            "ENABLE_EMAIL_AUTOCONFIRM": "false",
            "DISABLE_SIGNUP": "true",
            "CLOUD_DISABLE_SIGNUP": "true",
            "ENABLE_ANONYMOUS_USERS": "false",
            "ENABLE_PHONE_SIGNUP": "false",
            "SMTP_HOST": "127.0.0.1",
            "SMTP_USER": "staging-disabled",
            "SMTP_PASS": _secret(16),
            "CLOUD_SUPABASE_HTTP_PORT": "18131",
        }
        _private_write(temp / "supabase.env", _render_template(supabase))
        _private_write(temp / "litellm.env", "\n".join([
            "LITELLM_BIND_IP=10.9.8.1",
            "LITELLM_PUBLIC_PORT=4002",
            "SUPABASE_DOCKER_NETWORK=pskit-agent-supabase-staging_default",
            f"LITELLM_DB_PASSWORD={litellm_db_password}",
            f"LITELLM_MASTER_KEY=sk-{_secret(24)}",
            f"LITELLM_SALT_KEY=sk-{_secret(24)}",
        ]) + "\n")
        admin_dsn = f"postgresql://postgres:{quote(postgres_password, safe='')}@db:5432/postgres"
        app_dsn = f"postgresql://pskit_app:{quote(pskit_db_password, safe='')}@db:5432/postgres"
        _private_write(temp / "admin.env", "\n".join([
            f"SHARED_POSTGRES_ADMIN_DSN={admin_dsn}",
            f"LITELLM_DB_PASSWORD={litellm_db_password}",
            f"PSKIT_DB_PASSWORD={pskit_db_password}",
        ]) + "\n")
        _private_write(temp / "backend.env.base", "\n".join([
            "RESEARCH_AGENT_MODE=live",
            "RESEARCH_AGENT_RUNTIME=pi",
            "RESEARCH_AGENT_AF3_EXECUTOR=mock",
            "RESEARCH_AGENT_MCP_EXECUTOR=disabled",
            "RESEARCH_AGENT_ANONYMOUS_ENABLED=false",
            "RESEARCH_AGENT_AUTH_COOKIE_SECURE=false",
            "RESEARCH_AGENT_AUTH_ABUSE_MODE=observe",
            f"RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET={_secret(32)}",
            'RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON=["127.0.0.1/32"]',
            "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED=true",
            "TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA",
            'TURNSTILE_HOSTNAMES_JSON=["dummy-key-pass"]',
            f"RESEARCH_AGENT_ADMIN_API_KEY={_secret(32)}",
            f"RESEARCH_AGENT_DATABASE_URL={app_dsn}",
            f"SUPABASE_PUBLISHABLE_KEY={auth['SUPABASE_PUBLISHABLE_KEY']}",
            f"SUPABASE_SECRET_KEY={auth['SUPABASE_SECRET_KEY']}",
            "MODEL_GATEWAY_BASE_URL=http://gateway:4000/v1",
            "MODEL_GATEWAY_MODEL=claude-opus-4-8",
            "MODEL_GATEWAY_KIND=litellm",
        ]) + "\n")
        _private_write(temp / "cloud.env", "\n".join([
            f"AGENT_BACKEND_IMAGE={backend_image}",
            "AGENT_WEB_IMAGE=pskit-agent-web:unused-staging",
            f"AGENT_BACKEND_ENV_FILE={target / 'backend.env'}",
            f"AGENT_AF3_PROXY_KEY_FILE={target / 'proxy.env'}",
            "AGENT_PUBLIC_URL=http://10.9.8.1:18132",
            "TURNSTILE_SITE_KEY=1x00000000000000000000AA",
            "AGENT_PG_DATA_VOLUME=pskit-agent-staging_agent_data",
            "SUPABASE_DOCKER_NETWORK=pskit-agent-supabase-staging_default",
        ]) + "\n")
        _private_write(temp / "proxy.env", f"RESEARCH_AGENT_COMPUTE_CALLBACK_KEY={_secret(32)}\n")
        _private_write(temp / "seed.env", "\n".join([
            "STAGING_USER_EMAIL=staging-user@example.invalid",
            f"STAGING_USER_PASSWORD={_secret(24)}",
        ]) + "\n")
        manifest = {
            "backend_image": backend_image,
            "backend_image_id": image_id,
            "frontend_dist": str(dist),
            "frontend_dist_sha256": _dist_hash(dist),
            "projects": ["pskit-agent-supabase-staging", "pskit-agent-litellm-staging",
                         "pskit-agent-staging"],
        }
        _private_write(temp / "manifest.json", json.dumps(manifest, sort_keys=True) + "\n")
        _publish_directory(temp, target)
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", type=Path)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    options = parser.parse_args()
    prepare_staging(options.target_dir, backend_image=options.backend_image,
                    frontend_dist=options.frontend_dist)
    print("Private staging configuration created")


if __name__ == "__main__":
    main()
