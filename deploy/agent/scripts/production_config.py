"""Validate production authentication configuration without executing it or contacting services."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

# Official public dummy keys, checked 2026-10-10:
# https://developers.cloudflare.com/turnstile/troubleshooting/testing/
# Reject either kind in either field, including accidental site/secret swaps.
TURNSTILE_TEST_KEYS = frozenset({
    "1x00000000000000000000AA",
    "2x00000000000000000000AB",
    "1x00000000000000000000BB",
    "2x00000000000000000000BB",
    "3x00000000000000000000FF",
    "1x0000000000000000000000000000000AA",
    "2x0000000000000000000000000000000AA",
    "3x0000000000000000000000000000000AA",
})


def read_env(path: Path) -> dict[str, str]:
    """Read literal dotenv assignments; never source, expand or execute their contents."""
    if not path.is_file() or path.is_symlink():
        raise ValueError("Missing or unsafe configuration file")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) is None:
            raise ValueError(f"Invalid environment assignment at line {number}")
        if key in values:
            raise ValueError(f"Duplicate configuration key: {key}")
        value = value.strip()
        if value[:1] in {"'", '"'}:
            quote = value[0]
            closing = value.find(quote, 1)
            if closing == -1 or "\\" in value[:closing]:
                raise ValueError(f"Invalid quoted configuration: {key}")
            trailing = value[closing + 1:].strip()
            if trailing and not trailing.startswith("#"):
                raise ValueError(f"Invalid quoted configuration: {key}")
            value = value[1:closing]
        else:
            # Compose removes comments after whitespace for unquoted values.
            # An attached '#' remains literal (e.g. a quoted or unquoted password).
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def _required(values: Mapping[str, str], key: str) -> str:
    value = values.get(key, "")
    if (not value or value != value.strip() or "replace-with" in value or "$" in value
            or "`" in value or "\\" in value or any(ord(char) < 32 for char in value)):
        raise ValueError(f"Required configuration is unset: {key}")
    return value


def _json_list(values: Mapping[str, str], key: str) -> list[str]:
    try:
        value = json.loads(_required(values, key))
    except json.JSONDecodeError:
        raise ValueError(f"Invalid JSON configuration: {key}") from None
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Configuration must be a nonempty string list: {key}")
    return value


def validate_production_auth(backend: Mapping[str, str], cloud: Mapping[str, str]) -> None:
    """Require an enforcing application layer; this cannot certify live Nginx or CAPTCHA."""
    mode_key = "RESEARCH_AGENT_AUTH_ABUSE_MODE"
    if _required(backend, mode_key) != "enforce":
        raise ValueError(f"{mode_key} must be enforce in production")
    for key in ("RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET", "RESEARCH_AGENT_AUTH_CSRF_SECRET"):
        if len(_required(backend, key)) < 32:
            raise ValueError(f"{key} must contain at least 32 characters")
    if backend["RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET"] == backend["RESEARCH_AGENT_AUTH_CSRF_SECRET"]:
        raise ValueError("RESEARCH_AGENT_AUTH_CSRF_SECRET must be distinct from the rate-limit secret")
    proxy_key = "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON"
    if _json_list(backend, proxy_key) != ["127.0.0.1/32"]:
        raise ValueError(f"{proxy_key} must trust only the local Nginx proxy")
    captcha_key = "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED"
    if _required(backend, captcha_key) != "true":
        raise ValueError(f"{captcha_key} must be true in production")
    for values, key in ((backend, "TURNSTILE_SECRET_KEY"), (cloud, "TURNSTILE_SITE_KEY")):
        if _required(values, key) in TURNSTILE_TEST_KEYS:
            raise ValueError(f"{key} must not use a public Turnstile test key")
    host_key = "TURNSTILE_HOSTNAMES_JSON"
    hostnames = _json_list(backend, host_key)
    public_url = urlsplit(cloud.get("AGENT_PUBLIC_URL", "https://agent.bioailab.net"))
    if public_url.scheme != "https" or not public_url.hostname:
        raise ValueError("AGENT_PUBLIC_URL must identify the HTTPS production site")
    if public_url.hostname not in hostnames or any(
        name == "localhost" or name.endswith(".localhost")
        or re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", name) is None
        or "." not in name or re.fullmatch(r"[0-9.]+", name) is not None
        for name in hostnames
    ):
        raise ValueError(f"{host_key} must contain explicit production DNS hostnames")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-env-file", required=True, type=Path)
    parser.add_argument("--cloud-env-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        validate_production_auth(read_env(args.backend_env_file), read_env(args.cloud_env_file))
    except (OSError, UnicodeError, ValueError) as error:
        # Values, DSNs and credentials never appear in validation diagnostics.
        message = str(error) if isinstance(error, ValueError) else "Cannot read configuration file"
        print(message, file=sys.stderr)
        return 2
    print("Production authentication configuration passed; live protection remains unverified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
