"""Start and stop only the isolated PSKit staging Compose projects."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from deploy.agent.scripts.prepare_staging import _image_id

ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / "deploy/agent"
SUPABASE = ROOT / "infra/supabase"
LITELLM = ROOT / "infra/litellm"
NETWORK = "pskit-agent-supabase-staging_default"
PROJECTS = {
    "supabase": "pskit-agent-supabase-staging",
    "litellm": "pskit-agent-litellm-staging",
    "agent": "pskit-agent-staging",
}
PORTS = {"supabase": ("127.0.0.1", "18131"),
         "litellm": ("10.9.8.1", "4002"),
         "agent": ("127.0.0.1", "18090")}


def _private(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Private staging configuration is missing or unsafe")


def _env(path: Path) -> dict[str, str]:
    _private(path)
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _dsn_password(value: str) -> str:
    return urlsplit(value).password or ""


def _check_dsn(value: str, *, user: str, database: str) -> None:
    parsed = urlsplit(value)
    if (parsed.scheme != "postgresql" or parsed.username != user or
            not parsed.password or parsed.hostname != "db" or parsed.port != 5432 or
            parsed.path != f"/{database}" or parsed.query or parsed.fragment):
        raise ValueError("Staging database destination is unexpected")


def _check_prod_secret(value: str, fingerprints: Mapping[str, str]) -> None:
    if value and _hash(value) in fingerprints.values():
        raise ValueError("Staging secret matches production")


def validate_staging(
    manifest: Path, rendered: dict[str, dict], production_fingerprints: dict[str, str],
) -> None:
    """Check resolved Compose state and private secrets, without printing them."""
    if not production_fingerprints:
        raise ValueError("Production fingerprints are required before staging startup")
    _private(manifest)
    root = manifest.parent
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise ValueError("Staging directory has unsafe permissions")
    release = json.loads(manifest.read_text())
    if release.get("projects") != list(PROJECTS.values()) or set(rendered) != set(PROJECTS):
        raise ValueError("Unexpected staging projects")
    for part, project in PROJECTS.items():
        config = rendered[part]
        if config.get("name") != project:
            raise ValueError("Unexpected Compose project")
        if "10.9.8.2" in json.dumps(config):
            raise ValueError("Staging may not use A6000")
        forbidden_volumes = {"pskit-agent-db-data", "pskit-agent-storage",
                             "pskit-agent-cloud-pg17-20261003_agent_data"}
        for volume in config.get("volumes", {}).values():
            name = volume.get("name", "")
            if name in forbidden_volumes or not name.endswith("-staging") and not name.startswith(project + "_"):
                raise ValueError("Unexpected staging volume")
        allowed_networks = {
            "supabase": {"default": NETWORK},
            "litellm": {"supabase": NETWORK},
            "agent": {"app": "pskit-agent-staging_app", "supabase": NETWORK},
        }[part]
        if {key: item.get("name") for key, item in config.get("networks", {}).items()} != allowed_networks:
            raise ValueError("Unexpected staging network")
        for service in config.get("services", {}).values():
            service_networks = set(service.get("networks", {}))
            if service_networks != ({"default"} if part == "supabase" else
                                    {"supabase"} if part == "litellm" else
                                    {"app", "supabase"}):
                raise ValueError("Unexpected staging service network")
            for mount in service.get("volumes", []):
                source = mount.get("source", "")
                if mount.get("type") == "volume":
                    if source not in config.get("volumes", {}):
                        raise ValueError("Unexpected staging service volume")
                elif mount.get("type") == "bind":
                    path = Path(source).resolve()
                    allowed = (part == "supabase" and path.is_relative_to(SUPABASE / "volumes")
                               or part == "litellm" and path in {
                                   LITELLM / "config.staging.yaml", AGENT / "tests/mock_model_gateway.py"})
                    if not allowed or mount.get("read_only") is not True:
                        raise ValueError("Unexpected staging bind mount")
                else:
                    raise ValueError("Unexpected staging mount type")
            for port in service.get("ports", []):
                if port.get("host_ip") not in {"127.0.0.1", "10.9.8.1"}:
                    raise ValueError("Staging has a public bind")
                if str(port.get("published")) not in {item[1] for item in PORTS.values()}:
                    raise ValueError("Unexpected staging port")
    services = rendered["supabase"]["services"]
    container_names = [service["container_name"] for service in services.values()
                       if "container_name" in service]
    if len(container_names) != 11 or not all(name.endswith("-staging") for name in container_names):
        raise ValueError("Supabase container names are not isolated")
    if rendered["supabase"]["networks"]["default"]["name"] != NETWORK:
        raise ValueError("Supabase network is not isolated")
    for part in ("litellm", "agent"):
        if rendered[part]["networks"]["supabase"]["name"] != NETWORK:
            raise ValueError("Shared staging network mismatch")
    expected = {
        "supabase": ("api-gw", PORTS["supabase"]),
        "litellm": ("gateway", PORTS["litellm"]),
        "agent": ("backend", PORTS["agent"]),
    }
    for part, (service, (host, port)) in expected.items():
        ports = rendered[part]["services"][service].get("ports", [])
        if len(ports) != 1 or ports[0].get("host_ip") != host or str(ports[0].get("published")) != port:
            raise ValueError("Staging service port mismatch")
    if rendered["supabase"]["volumes"]["agent-db-data"]["name"] != "pskit-agent-db-data-staging":
        raise ValueError("Staging database volume mismatch")
    if rendered["supabase"]["volumes"]["agent-storage"]["name"] != "pskit-agent-storage-staging":
        raise ValueError("Staging storage volume mismatch")
    if rendered["agent"]["volumes"]["agent_data"]["name"] != "pskit-agent-staging_agent_data":
        raise ValueError("Staging Agent volume mismatch")
    if rendered["agent"]["services"]["backend"]["environment"].get("RESEARCH_AGENT_AF3_EXECUTOR") != "mock":
        raise ValueError("Real AF3 is prohibited in staging")
    if "af3-callback-proxy" in rendered["agent"]["services"] or "web" in rendered["agent"]["services"]:
        raise ValueError("Unexpected staging service")
    if "model-stub" not in rendered["litellm"]["services"]:
        raise ValueError("Staging model stub is missing")
    if (rendered["agent"]["services"]["backend"].get("image") != release.get("backend_image")
            or rendered["litellm"]["services"]["model-stub"].get("image") != release.get("backend_image")):
        raise ValueError("Staging image differs from release manifest")

    supabase_env = _env(root / "supabase.env")
    litellm_env = _env(root / "litellm.env")
    backend_path = root / "backend.env"
    backend_env = _env(backend_path if backend_path.exists() else root / "backend.env.base")
    admin_env = _env(root / "admin.env")
    _check_dsn(admin_env.get("SHARED_POSTGRES_ADMIN_DSN", ""), user="postgres", database="postgres")
    _check_dsn(backend_env.get("RESEARCH_AGENT_DATABASE_URL", ""),
               user="pskit_app", database="postgres")
    _check_dsn(rendered["agent"]["services"]["backend"]["environment"].get(
        "RESEARCH_AGENT_DATABASE_URL", ""), user="pskit_app", database="postgres")
    _check_dsn(rendered["litellm"]["services"]["gateway"]["environment"].get(
        "DATABASE_URL", ""), user="litellm", database="litellm")
    if backend_env.get("RESEARCH_AGENT_AUTH_ABUSE_MODE") not in {"observe", "enforce"}:
        raise ValueError("Staging auth abuse mode is unsafe")
    if len(backend_env.get("RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET", "")) < 32:
        raise ValueError("Staging auth rate-limit secret is missing")
    if backend_env.get("RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON") != '["127.0.0.1/32"]':
        raise ValueError("Staging trusted proxy boundary is unexpected")
    if backend_env.get("RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED") != "true":
        raise ValueError("Staging CAPTCHA must remain enabled")
    if backend_env.get("TURNSTILE_SECRET_KEY") != "1x0000000000000000000000000000000AA":
        raise ValueError("Staging must use the Turnstile test secret")
    if backend_env.get("TURNSTILE_HOSTNAMES_JSON") != '["dummy-key-pass"]':
        raise ValueError("Staging Turnstile hostname is unexpected")
    for value in (
        supabase_env.get("POSTGRES_PASSWORD", ""), supabase_env.get("JWT_SECRET", ""),
        supabase_env.get("SUPABASE_SECRET_KEY", ""),
        supabase_env.get("SUPABASE_PUBLISHABLE_KEY", ""),
        litellm_env.get("LITELLM_MASTER_KEY", ""), litellm_env.get("LITELLM_SALT_KEY", ""),
        admin_env.get("PSKIT_DB_PASSWORD", ""), admin_env.get("LITELLM_DB_PASSWORD", ""),
        backend_env.get("MODEL_GATEWAY_API_KEY", ""),
        _dsn_password(backend_env.get("RESEARCH_AGENT_DATABASE_URL", "")),
        _dsn_password(admin_env.get("SHARED_POSTGRES_ADMIN_DSN", "")),
        _dsn_password(rendered["agent"]["services"]["backend"]["environment"].get(
            "RESEARCH_AGENT_DATABASE_URL", "")),
    ):
        _check_prod_secret(value, production_fingerprints)
    for config in rendered.values():
        for service in config.get("services", {}).values():
            for value in service.get("environment", {}).values():
                if isinstance(value, str):
                    _check_prod_secret(value, production_fingerprints)
                    if "://" in value:
                        _check_prod_secret(_dsn_password(value), production_fingerprints)


def _production_fingerprints() -> dict[str, str]:
    result = {}
    for path in (SUPABASE / ".env", LITELLM / ".env.shared", AGENT / "cloud.backend.env"):
        if not path.exists():
            continue
        for key, value in _env(path).items():
            if value and any(term in key for term in
                             ("PASSWORD", "SECRET", "KEY", "TOKEN")):
                result[f"{path.name}:{key}"] = _hash(value)
            if key in {"RESEARCH_AGENT_DATABASE_URL", "SHARED_POSTGRES_ADMIN_DSN"}:
                result[f"{path.name}:{key}:password"] = _hash(_dsn_password(value))
    if not (SUPABASE / ".env").exists():
        raise ValueError("Production Supabase configuration is unavailable for comparison")
    return result


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("Staging command failed; inspect staging container logs") from exc
    return result.stdout


def _compose_commands(root: Path, env_file: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    # Compose gives shell variables precedence over --env-file. Keep the shell
    # environment narrow so an operator's production secrets cannot override
    # the staging files during interpolation.
    passthrough = {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
                   "XDG_RUNTIME_DIR"}
    agent_env = {key: value for key, value in os.environ.items() if key in passthrough}
    cloud_env = _env(root / "cloud.env")
    allowed_cloud_keys = {"AGENT_BACKEND_IMAGE", "AGENT_WEB_IMAGE", "AGENT_BACKEND_ENV_FILE",
                          "AGENT_AF3_PROXY_KEY_FILE", "AGENT_PUBLIC_URL",
                          "AGENT_PG_DATA_VOLUME", "SUPABASE_DOCKER_NETWORK",
                          "TURNSTILE_SITE_KEY"}
    if set(cloud_env) != allowed_cloud_keys:
        raise ValueError("Unexpected Staging Compose interpolation variable")
    agent_env.update(cloud_env)
    agent_env["AGENT_BACKEND_ENV_FILE"] = str(env_file)
    agent_env["SUPABASE_DOCKER_NETWORK"] = NETWORK
    agent_env["STAGING_MODEL_IMAGE"] = agent_env["AGENT_BACKEND_IMAGE"]
    litellm_env = _env(root / "litellm.env")
    if litellm_env.get("SUPABASE_DOCKER_NETWORK") != NETWORK:
        raise ValueError("LiteLLM network mismatch")
    commands = {
        "supabase": ["docker", "compose", "--env-file", str(root / "supabase.env"),
                     "-f", str(SUPABASE / "docker-compose.yml"),
                     "-f", str(SUPABASE / "compose.cloud.yaml"),
                     "-f", str(SUPABASE / "compose.staging.yaml"),
                     "-p", PROJECTS["supabase"]],
        "litellm": ["docker", "compose", "--env-file", str(root / "litellm.env"),
                    "-f", str(LITELLM / "compose.shared-postgres.yaml"),
                    "-f", str(LITELLM / "compose.staging.yaml"),
                    "-p", PROJECTS["litellm"]],
        "agent": ["docker", "compose", "--env-file", str(root / "cloud.env"),
                  "-f", str(AGENT / "compose.yaml"),
                  "-f", str(AGENT / "compose.cloud.yaml"),
                  "-f", str(AGENT / "compose.postgres.yaml"),
                  "-f", str(AGENT / "compose.staging.yaml"),
                  "-p", PROJECTS["agent"]],
    }
    return commands, agent_env


def _render_configs(commands: dict[str, list[str]], env: dict[str, str]) -> dict[str, dict]:
    return {part: json.loads(_run(command + ["config", "--format", "json"], env=env))
            for part, command in commands.items()}


def _check_resources(root: Path) -> None:
    memory = next(int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                  if line.startswith("MemAvailable:"))
    if memory < 4 * 1024**3 or shutil.disk_usage(root).free < 10 * 1024**3:
        raise RuntimeError("Insufficient memory or disk for staging")


def _check_ports() -> None:
    for host, port in PORTS.values():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, int(port)))
            except OSError as exc:
                raise RuntimeError("Staging port or WireGuard address is unavailable") from exc


def _bootstrap_key(root: Path) -> Path:
    key = root / "virtual-key"
    _run(["python", str(LITELLM / "bootstrap_pskit.py"),
          "--base-url", "http://10.9.8.1:4002", "--env-file", str(root / "litellm.env"),
          "--key-file", str(key)])
    _private(key)
    return key


def _backend_env(root: Path, key: str) -> None:
    destination = root / "backend.env"
    content = (root / "backend.env.base").read_text().rstrip("\n") + f"\nMODEL_GATEWAY_API_KEY={key}\n"
    if destination.exists() or destination.is_symlink():
        _private(destination)
        if destination.read_text() != content:
            raise ValueError("Existing staging backend configuration differs")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".backend-env-", dir=root)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        os.unlink(temporary)


def run_staging(
    command: str, config_dir: Path, *, production_fingerprints: dict[str, str] | None = None,
    log_part: str | None = None,
) -> None:
    root = Path(config_dir).absolute()
    manifest = root / "manifest.json"
    _private(manifest)
    release = json.loads(manifest.read_text())
    if command == "down":
        commands, env = _compose_commands(root, root / "backend.env.base")
        for part in ("agent", "litellm", "supabase"):
            _run(commands[part] + ["down"], env=env)
        return
    env_file = root / "backend.env" if (root / "backend.env").exists() else root / "backend.env.base"
    commands, env = _compose_commands(root, env_file)
    if command == "status":
        for part in PROJECTS:
            print(f"[{part}]\n{_run(commands[part] + ['ps'], env=env)}")
        return
    if command == "logs":
        if log_part not in PROJECTS:
            raise ValueError("Expected logs supabase|litellm|agent")
        print(_run(commands[log_part] + ["logs", "--tail=100"], env=env))
        return
    if command != "up":
        raise ValueError("Expected up|status|logs|down")
    fingerprints = production_fingerprints if production_fingerprints is not None else _production_fingerprints()
    rendered = _render_configs(commands, env)
    validate_staging(manifest, rendered, fingerprints)
    if _image_id(release["backend_image"]) != release["backend_image_id"]:
        raise ValueError("Backend image ID differs from the staging release")
    _check_resources(root)
    _check_ports()
    _run(commands["supabase"] + ["up", "-d", "--wait"], env=env)
    provision = ["docker", "run", "--rm", "--network", NETWORK,
                 "--env-file", str(root / "admin.env"), "--read-only", "--cap-drop", "ALL",
                 "--security-opt", "no-new-privileges", "-v",
                 f"{AGENT / 'scripts/provision_shared_postgres.py'}:/app/provision_shared_postgres.py:ro",
                 "--entrypoint", "python", release["backend_image"],
                 "/app/provision_shared_postgres.py"]
    _run(provision, env=env)
    migrate = ["docker", "run", "--rm", "--network", NETWORK,
               "--env-file", str(root / "admin.env"), "--read-only", "--cap-drop", "ALL",
               "--security-opt", "no-new-privileges", "--entrypoint", "python",
               release["backend_image"], "-c",
                 ('import os; from app.db.postgres_migrations import migrate_postgres; '
                  'migrate_postgres(os.environ["SHARED_POSTGRES_ADMIN_DSN"])')]
    _run(migrate, env=env)
    _run(commands["litellm"] + ["up", "-d", "--wait"], env=env)
    key_path = _bootstrap_key(root)
    _backend_env(root, key_path.read_text().strip())
    final_commands, final_env = _compose_commands(root, root / "backend.env")
    validate_staging(manifest, _render_configs(final_commands, final_env), fingerprints)
    _run(final_commands["agent"] + ["up", "-d", "--wait", "backend"], env=final_env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["up", "status", "logs", "down"])
    parser.add_argument("part", nargs="?", choices=["supabase", "litellm", "agent"])
    arguments = parser.parse_args()
    config = os.environ.get("STAGING_CONFIG_DIR", "/home/ecs-user/pskit-agent-staging-private")
    run_staging(arguments.command, Path(config), log_part=arguments.part)


if __name__ == "__main__":
    main()
