#!/usr/bin/env python3
"""Exercise the full Staging Tool Product lifecycle through HTTP APIs."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import yaml


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    token: str,
    *,
    json_body: Any | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = await client.request(
        method,
        path,
        headers={**_headers(token), **(headers or {})},
        json=json_body,
    )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("SMOKE_RESPONSE_NOT_OBJECT")
    return value


async def run_smoke(
    client: httpx.AsyncClient,
    *,
    admin_token: str,
    user_token: str,
    service_id: str,
    probe_uri: str,
    network_zone: str,
    product_id: str,
    slug: str,
    action_id: str,
    draft: dict[str, Any],
    suite: dict[str, Any],
    arguments: dict[str, Any],
    poll_seconds: float = 1,
    timeout_seconds: float = 300,
) -> dict[str, Any]:
    """Onboard, qualify, publish, run, recover and suspend one Staging product."""

    probe = await _request(
        client,
        "POST",
        "/api/v1/admin/mcp-probes",
        admin_token,
        json_body={
            "service_id": service_id,
            "uri": probe_uri,
            "transport": "streamable_http",
            "credential_ref": None,
            "network_zone": network_zone,
        },
    )
    service_revision = int(probe["service_revision"])
    await _request(
        client,
        "POST",
        f"/api/v1/admin/services/{service_id}/discoveries",
        admin_token,
        json_body={"service_revision": service_revision, "reason": "Staging Tool Product smoke"},
    )

    effective_draft = copy.deepcopy(draft)
    for binding in effective_draft.get("bindings", []):
        binding["service_id"] = service_id
        binding["service_revision"] = service_revision
    saved = await _request(
        client,
        "PUT",
        f"/api/v1/admin/tool-products/{product_id}/draft",
        admin_token,
        json_body={
            "expected_revision": max(0, int(effective_draft.get("revision", 1)) - 1),
            "reason": "Staging Tool Product smoke",
            "draft": effective_draft,
            "acceptance_suite": suite,
        },
    )
    revision = int(saved.get("revision", effective_draft.get("revision", 1)))
    qualification = await _request(
        client,
        "POST",
        f"/api/v1/admin/tool-products/{product_id}/qualifications",
        admin_token,
        json_body={
            "revision": revision,
            "suite_revision": int(suite["revision"]),
            "reason": "Staging Tool Product smoke",
        },
    )
    if qualification.get("status") != "passed":
        raise RuntimeError("QUALIFICATION_FAILED")

    report_id = str(qualification["report_id"])
    release = await _request(
        client,
        "POST",
        f"/api/v1/admin/tool-product-releases/{report_id}/publish",
        admin_token,
        json_body={
            "product_id": product_id,
            "revision": revision,
            "reason": "Staging Tool Product smoke",
        },
    )
    release_id = str(release["release_id"])
    await _request(client, "GET", f"/api/v1/tool-products/{slug}", user_token)
    started = await _request(
        client,
        "POST",
        f"/api/v1/tool-products/{slug}/actions/{action_id}/runs",
        user_token,
        json_body={"arguments": arguments},
        headers={"Idempotency-Key": f"staging-smoke-{time.time_ns()}"},
    )
    run_id = str(started["run_id"])

    cursor = 0
    deadline = time.monotonic() + timeout_seconds
    terminal: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        snapshot = await _request(client, "GET", f"/api/v1/tool-runs/{run_id}", user_token)
        events_response = await client.get(
            f"/api/v1/tool-runs/{run_id}/events",
            params={"cursor": cursor},
            headers=_headers(user_token),
        )
        events_response.raise_for_status()
        events = events_response.json()
        if not isinstance(events, list):
            raise RuntimeError("SMOKE_EVENTS_NOT_LIST")
        if events:
            cursor = max(cursor, *(int(item["sequence"]) for item in events))
        if snapshot.get("status") in {"completed", "failed", "cancelled"}:
            terminal = snapshot
            break
        await asyncio.sleep(poll_seconds)
    if terminal is None:
        raise RuntimeError("RUN_TIMEOUT")
    if terminal.get("status") != "completed":
        raise RuntimeError(f"RUN_{str(terminal.get('status')).upper()}")
    artifacts = terminal.get("artifacts")
    usage = terminal.get("usage")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("ARTIFACT_EVIDENCE_MISSING")
    if not isinstance(usage, dict) or not usage.get("source"):
        raise RuntimeError("USAGE_EVIDENCE_MISSING")

    await _request(
        client,
        "POST",
        f"/api/v1/admin/tool-product-releases/{release_id}/suspend",
        admin_token,
        json_body={"reason": "Staging smoke cleanup"},
    )
    return {
        "release_id": release_id,
        "run_id": run_id,
        "result": terminal.get("result"),
        "artifact_ids": [str(item["id"]) for item in artifacts],
        "usage_source": str(usage["source"]),
        "binding_digest": qualification["binding_digest"],
        "ui_digest": qualification["ui_digest"],
        "suite_digest": qualification["suite_digest"],
    }


def _yaml(path: str) -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


async def _main(args: argparse.Namespace) -> None:
    draft = _yaml(args.draft)
    acceptance = _yaml(args.acceptance)
    suite = acceptance.get("suite", acceptance)
    arguments = json.loads(args.arguments)
    async with httpx.AsyncClient(base_url=args.base_url, timeout=args.timeout_seconds) as client:
        result = await run_smoke(
            client,
            admin_token=args.admin_token,
            user_token=args.user_token,
            service_id=args.service_id,
            probe_uri=args.probe_uri,
            network_zone=args.network_zone,
            product_id=args.product_id,
            slug=args.slug,
            action_id=args.action_id,
            draft=draft,
            suite=suite,
            arguments=arguments,
            poll_seconds=args.poll_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--admin-token", default=os.getenv("PSKIT_SMOKE_ADMIN_TOKEN"))
    parser.add_argument("--user-token", default=os.getenv("PSKIT_SMOKE_USER_TOKEN"))
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--probe-uri", required=True)
    parser.add_argument("--network-zone", default="public")
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--arguments", required=True)
    parser.add_argument("--poll-seconds", type=float, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parsed = parser.parse_args()
    if not parsed.admin_token or not parsed.user_token:
        parser.error("set PSKIT_SMOKE_ADMIN_TOKEN and PSKIT_SMOKE_USER_TOKEN")
    asyncio.run(_main(parsed))
