#!/usr/bin/env python3
"""Publish locally built OpenSandbox images to the host-loopback registry."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.request

REGISTRY = "127.0.0.1:5000"
RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class PublishError(RuntimeError):
    """The local release registry or image contract is invalid."""


def _run(command: list[str]) -> str:
    completed = subprocess.run(
        command, capture_output=True, text=True, check=False
    )
    if completed.returncode:
        raise PublishError("Docker image publication failed")
    return completed.stdout.strip()


def _registry_ready() -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{REGISTRY}/v2/", timeout=3) as response:
            if response.status != 200:
                raise PublishError("Loopback release registry is unavailable")
    except OSError as exc:
        raise PublishError("Loopback release registry is unavailable") from exc


def _publish(source: str, repository: str, release: str) -> dict[str, str]:
    image_id = _run(["docker", "image", "inspect", "--format", "{{.Id}}", source])
    if DIGEST.fullmatch(image_id) is None:
        raise PublishError("Source image is unavailable or mutable")
    target = f"{REGISTRY}/pskit/{repository}:{release}"
    _run(["docker", "tag", source, target])
    _run(["docker", "push", target])
    digests = json.loads(_run([
        "docker", "image", "inspect", "--format", "{{json .RepoDigests}}", target
    ]))
    prefix = f"{REGISTRY}/pskit/{repository}@"
    reference = next((item for item in digests if item.startswith(prefix)), "")
    if not reference or DIGEST.fullmatch(reference.removeprefix(prefix)) is None:
        raise PublishError("Published image has no immutable registry digest")
    return {"reference": reference, "image_id": image_id}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--sandbox-image", required=True)
    args = parser.parse_args()
    if RELEASE.fullmatch(args.release) is None:
        raise SystemExit("Release name is invalid")
    try:
        _registry_ready()
        result = {
            "registry": REGISTRY,
            "server": _publish(args.server_image, "opensandbox-server", args.release),
            "sandbox": _publish(args.sandbox_image, "workspace", args.release),
        }
    except (OSError, ValueError, json.JSONDecodeError, PublishError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
