"""Create the PSKit LiteLLM budgets and backend virtual key once.

Run from infra/litellm on the gateway host. The provider keys are configured
separately in the Admin UI. Never print the master or generated virtual key.
"""

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path.cwd()
BASE_URL = "http://127.0.0.1:4000"
BUDGET_ID = "pskit-member-monthly"
TEAM_ID = "pskit-lab"


def _master_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("LITELLM_MASTER_KEY="):
            return line.partition("=")[2]
    raise RuntimeError("LITELLM_MASTER_KEY is missing")


def _request(path: str, *, payload: dict | None = None) -> object:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=body,
        headers={
            "Authorization": f"Bearer {_master_key()}",
            "Content-Type": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Response bodies can contain credentials. Keep errors terse.
        raise RuntimeError(f"{path}: HTTP {error.code}") from None


def _verify_existing_key(key: str) -> None:
    """Ask this gateway to authenticate a saved key before trusting it."""
    request = urllib.request.Request(
        f"{BASE_URL}/models", headers={"Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise RuntimeError("Saved virtual key is invalid for this gateway")
    except urllib.error.HTTPError:
        raise RuntimeError("Saved virtual key is invalid for this gateway") from None


def main(argv: list[str] | None = None) -> None:
    global BASE_URL
    parser = argparse.ArgumentParser(description="Bootstrap PSKit LiteLLM budgets and key")
    parser.add_argument("--base-url", help="Candidate gateway URL, such as http://10.9.8.1:4001")
    parser.add_argument("--key-file", type=Path, help="New 0600 backend virtual key file")
    arguments = parser.parse_args(argv)
    bind_ip = next(
        (line.partition("=")[2] for line in (ROOT / ".env").read_text().splitlines()
         if line.startswith("LITELLM_BIND_IP=")),
        "127.0.0.1",
    )
    base_url = arguments.base_url or f"http://{bind_ip}:4000"
    parsed = urllib.parse.urlsplit(base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Invalid LiteLLM base URL")
    BASE_URL = base_url.rstrip("/")
    key_file = arguments.key_file or ROOT / ".pskit-virtual-key"
    if not key_file.is_absolute():
        key_file = ROOT / key_file
    legacy_file = ROOT / ".pskit-virtual-key"
    if parsed.port != 4000 and key_file.resolve() == legacy_file.resolve():
        raise ValueError("Candidate gateway needs a new key file, not the legacy key")

    if key_file.exists():
        if key_file.is_symlink() or key_file.stat().st_mode & 0o077:
            raise RuntimeError("Virtual key file has unsafe permissions")
        existing_key = key_file.read_text().strip()
        if not existing_key.startswith("sk-"):
            raise RuntimeError("Saved virtual key has an invalid format")
        _verify_existing_key(existing_key)

    budgets = _request("/budget/info", payload={"budgets": [BUDGET_ID]})
    if not isinstance(budgets, list):
        raise TypeError("Unexpected budget/info response")
    if budgets:
        budget = budgets[0]
        if budget.get("max_budget") != 2:
            raise RuntimeError("Existing user budget differs from $2")
    else:
        budget = _request(
            "/budget/new",
            payload={"budget_id": BUDGET_ID, "max_budget": 2, "budget_duration": "30d"},
        )
        if not isinstance(budget, dict) or budget.get("budget_id") != BUDGET_ID:
            raise RuntimeError("User budget was not confirmed")
    print("Default end-user budget: $2 / 30d")

    try:
        team = _request(f"/team/info?team_id={TEAM_ID}")
    except RuntimeError as error:
        if str(error) != f"/team/info?team_id={TEAM_ID}: HTTP 404":
            raise
        team = _request(
            "/team/new",
            payload={
                "team_id": TEAM_ID,
                "team_alias": TEAM_ID,
                "max_budget": 10,
                "budget_duration": "30d",
            },
        )
    if not isinstance(team, dict) or team.get("team_id") != TEAM_ID:
        raise RuntimeError("Team was not confirmed")
    team_info = team.get("team_info", team)
    if not isinstance(team_info, dict) or team_info.get("max_budget") != 10:
        raise RuntimeError("Existing team budget differs from $10")
    print("PSKit team budget: $10 / 30d")

    if key_file.exists():
        print("Backend virtual key verified on this gateway")
        return
    response = _request(
        "/key/generate",
        payload={"team_id": TEAM_ID, "key_alias": "pskit-backend"},
    )
    key = response.get("key") if isinstance(response, dict) else None
    if not isinstance(key, str) or not key.startswith("sk-"):
        raise RuntimeError("Virtual key was not returned")
    descriptor = os.open(
        key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
    )
    with os.fdopen(descriptor, "w") as output:
        output.write(key + "\n")
    print("Backend virtual key saved with mode 0600")


if __name__ == "__main__":
    main()
