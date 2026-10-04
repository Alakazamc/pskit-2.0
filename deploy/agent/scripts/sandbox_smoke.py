"""Run real Docker/Pi lifecycle evidence in a fresh isolated project with a local model stub."""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "new_backend"))


class DockerHttpTransport(httpx.AsyncBaseTransport):
    """Send real HTTP from the test backend network when Docker is hosted outside WSL."""

    def __init__(self, backend_container):
        self.backend_container = backend_container

    async def handle_async_request(self, request):
        import httpx

        payload = {
            "method": request.method,
            "url": "http://sandbox-manager:8090" + request.url.raw_path.decode(),
            "headers": dict(request.headers),
            "body": base64.b64encode(await request.aread()).decode(),
        }
        script = """import base64,json,sys,urllib.request,urllib.error
p=json.load(sys.stdin)
r=urllib.request.Request(p['url'],data=base64.b64decode(p['body']) if p['method']!='GET' else None,
                         headers=p['headers'],method=p['method'])
try: response=urllib.request.urlopen(r,timeout=50)
except urllib.error.HTTPError as e: response=e
print(json.dumps({'status':response.status,'headers':dict(response.headers),
                  'body':base64.b64encode(response.read()).decode()}))
"""
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-i",
            self.backend_container,
            "python",
            "-c",
            script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(json.dumps(payload).encode())
        if process.returncode:
            raise httpx.ConnectError(
                "Isolated HTTP probe failed: " + stderr.decode()[-1000:], request=request
            )
        result = json.loads(stdout)
        return httpx.Response(
            result["status"],
            headers=result["headers"],
            content=base64.b64decode(result["body"]),
            request=request,
        )

    async def aclose(self):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--gateway-image", required=True)
    parser.add_argument("--user", default="sandbox-test-user")
    parser.add_argument("--session", default="sandbox-test-session")
    parser.add_argument("--manager-port", type=int, default=18890)
    args = parser.parse_args()
    if not re.fullmatch(r"pskit-sandbox-test-[a-z0-9-]{1,12}", args.project):
        parser.error("Only fresh pskit-sandbox-test-* projects are permitted")
    for value in (args.user, args.session):
        if not re.fullmatch(r"sandbox-test-[A-Za-z0-9_-]+", value):
            parser.error("Only sandbox-test-* user/session IDs are permitted")
    if not re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", args.image):
        parser.error("Smoke image must be an immutable digest or local sha256 image ID")
    if not re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", args.gateway_image):
        parser.error("Gateway image must be an immutable digest or local sha256 image ID")
    namespace = "test-" + args.project.removeprefix("pskit-sandbox-test-")
    manager_token = secrets.token_hex(24)
    password = secrets.token_hex(16)
    env = {
        **os.environ,
        "AGENT_COMPOSE_PROJECT": args.project,
        "AGENT_SANDBOX_IMAGE": args.image,
        "AGENT_SANDBOX_GATEWAY_IMAGE": args.gateway_image,
        "AGENT_SANDBOX_NAMESPACE": namespace,
        "AGENT_SANDBOX_NETWORK": args.project + "_sandbox",
        "AGENT_SANDBOX_MANAGER_TOKEN": manager_token,
        "AGENT_SANDBOX_BRIDGE_SECRET": secrets.token_hex(24),
        "SANDBOX_TEST_DATABASE_PASSWORD": password,
        "AGENT_SANDBOX_POSTGRES_DSN": f"postgresql://sandbox_test:{password}@database:5432/sandbox_test",
        "SANDBOX_TEST_MANAGER_PORT": str(args.manager_port),
        "SANDBOX_TEST_DATABASE_IMAGE": "postgres:16-alpine",
    }
    compose = [
        "docker",
        "compose",
        "-p",
        args.project,
        "-f",
        str(ROOT / "deploy/agent/tests/compose.sandbox.test.yaml"),
    ]

    def command(arguments, *, capture=False):
        return subprocess.run(arguments, env=env, check=True, capture_output=capture, text=True)

    existing = command(compose + ["ps", "--all", "--quiet"], capture=True)
    if existing.stdout.strip():
        parser.error("Choose a fresh project; the requested project already has containers")
    database_image = command(
        ["docker", "image", "inspect", "postgres:16-alpine", "--format", "{{.Id}}"], capture=True
    ).stdout.strip()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", database_image):
        raise RuntimeError("Cached PostgreSQL image did not resolve to an immutable local ID")
    env["SANDBOX_TEST_DATABASE_IMAGE"] = database_image
    owners = [args.user, args.user + "-other"]
    instances = [
        f"pskit-sbx-{namespace}-" + hashlib.sha256(f"{namespace}:{owner}".encode()).hexdigest()[:24]
        for owner in owners
    ]
    try:
        command(compose + ["up", "-d", "--wait", "database", "backend", "sandbox-gateway"])
        command(
            compose
            + [
                "run",
                "--rm",
                "--no-deps",
                "sandbox-manager",
                "python",
                "-c",
                (
                    "import os; from app.db.postgres_migrations import migrate_postgres; "
                    "migrate_postgres(os.environ['PSKIT_SANDBOX_POSTGRES_DSN'],schema='sandbox_test')"
                ),
            ]
        )
        command(compose + ["up", "-d", "sandbox-manager"])

        async def checks():
            import httpx
            from app.adapters.live.sandbox_pi import SandboxPiRunner

            url = f"http://127.0.0.1:{args.manager_port}"
            async with httpx.AsyncClient(
                base_url=url,
                headers={"Authorization": f"Bearer {manager_token}"},
                timeout=30,
                transport=DockerHttpTransport(args.project + "-backend-1"),
                trust_env=False,
            ) as client:
                for _ in range(60):
                    try:
                        if (await client.get("/health")).status_code == 200:
                            break
                    except httpx.RequestError:
                        pass
                    await asyncio.sleep(0.25)
                else:
                    raise RuntimeError("Isolated manager did not start")
                runner = SandboxPiRunner(
                    manager_url=url,
                    manager_token=manager_token,
                    model="sandbox-stub",
                    timeout_seconds=45,
                    manager_transport=DockerHttpTransport(args.project + "-backend-1"),
                )

                def environment(owner, run):
                    return {
                        "PSKIT_USER_ID": owner,
                        "PSKIT_RUN_ID": run,
                        "PSKIT_AGENT_TOOL_TOKEN": "test-run-token",
                        "MODEL_GATEWAY_API_KEY": run + ".test-run-token",
                        "PSKIT_AF3_ENABLED": "0",
                    }

                transcripts = []
                for owner, session in [
                    (owners[0], args.session),
                    (owners[0], args.session + "-two"),
                    (owners[1], args.session + "-three"),
                ]:
                    result = await runner.prompt(
                        session,
                        "sandbox smoke",
                        lambda _: None,
                        environment=environment(owner, "run-" + session),
                    )
                    assert result["text"] == "Sandbox local model ready."
                    assert f"/{session}/.pi/" in result["session_file"]
                    transcripts.append(result["session_file"])
                summaries = (await client.get("/v1/sandboxes")).json()
                assert len(summaries) == 2
                by_owner = {item["owner_id"]: item for item in summaries}
                assert set(instances) == {item["instance_id"] for item in summaries}
                raw = b">A\nACDE"
                digest = hashlib.sha256(raw).hexdigest()
                await runner.workspace_request(
                    owners[0],
                    "POST",
                    "/v1/workspace/files",
                    json={
                        "session_id": args.session,
                        "relative_path": "files/input.fasta",
                        "sha256": digest,
                        "content_b64": base64.b64encode(raw).decode(),
                    },
                )
                name = by_owner[owners[0]]["instance_id"]
                script = (
                    "from pathlib import Path; p=Path('/workspace/sessions')/"
                    + repr(args.session)
                    + "; "
                    "raw=(p/'files/input.fasta').read_bytes(); assert raw==b'>A\\nACDE'; "
                    "d=p/'artifacts/smoke-attempt'; d.mkdir(parents=True); (d/'result.fasta').write_bytes(raw)"
                )
                command(["docker", "exec", name, "python", "-c", script])
                exported = await runner.workspace_request(
                    owners[0],
                    "GET",
                    "/v1/workspace/artifacts",
                    params={"session_id": args.session, "attempt_id": "smoke-attempt"},
                )
                assert base64.b64decode(exported["files"][0]["content_b64"]) == raw
                network_probe = """import socket,urllib.request
for host in ['database','backend','api-db','sandbox-manager']:
    if host=='sandbox-manager': continue
    try: socket.getaddrinfo(host,5432)
    except socket.gaierror: pass
    else: raise AssertionError('Forbidden network name resolves: '+host)
try: socket.create_connection(('1.1.1.1',443),timeout=1)
except OSError: pass
else: raise AssertionError('Internet egress accessible')
try: urllib.request.urlopen('http://sandbox-gateway:8080/api/v1/admin/users')
except urllib.error.HTTPError as e: assert e.code==403
else: raise AssertionError('Gateway exposes admin route')
"""
                command(["docker", "exec", name, "python", "-c", network_probe])
                for container in instances:
                    inspected = json.loads(
                        command(["docker", "inspect", container], capture=True).stdout
                    )[0]
                    assert set(inspected["NetworkSettings"]["Networks"]) == {
                        env["AGENT_SANDBOX_NETWORK"]
                    }
                # A real Pi turn exceeds the shortened test idle window.
                active = asyncio.create_task(
                    runner.prompt(
                        args.session,
                        "sandbox_wait_probe",
                        lambda _: None,
                        session_file=transcripts[0],
                        environment=environment(owners[0], "run-wait"),
                    )
                )
                await asyncio.sleep(1.5)
                sweep = (await client.post("/v1/sandboxes/sweep")).json()
                running = json.loads(command(["docker", "inspect", name], capture=True).stdout)[0][
                    "State"
                ]["Running"]
                assert running is True
                result = await active
                volume = by_owner[owners[0]]["volume_id"]
                summary = next(
                    item
                    for item in (await client.get("/v1/sandboxes")).json()
                    if item["owner_id"] == owners[0]
                )
                response = await client.post(
                    f"/v1/sandboxes/{owners[0]}/drain",
                    json={"expected_revision": summary["revision"]},
                )
                response.raise_for_status()
                response = await client.post(
                    f"/v1/sandboxes/{owners[0]}/replace", json={"image_digest": args.image}
                )
                response.raise_for_status()
                assert response.json()["state"] == "completed"
                resumed = await runner.prompt(
                    args.session,
                    "resume after upgrade",
                    lambda _: None,
                    session_file=result["session_file"],
                    environment=environment(owners[0], "run-resume"),
                )
                assert resumed["text"] == "Sandbox local model ready."
                await asyncio.sleep(1.1)
                stopped = (await client.post("/v1/sandboxes/sweep")).json()
                assert stopped["stopped"] >= 1
                restarted = await runner.prompt(
                    args.session,
                    "resume after stop",
                    lambda _: None,
                    session_file=resumed["session_file"],
                    environment=environment(owners[0], "run-restart"),
                )
                assert restarted["text"] == "Sandbox local model ready."
                final = next(
                    item
                    for item in (await client.get("/v1/sandboxes")).json()
                    if item["owner_id"] == owners[0]
                )
                assert final["volume_id"] == volume
                print(
                    json.dumps(
                        {
                            "project": args.project,
                            "database_image": database_image,
                            "two_users_three_sessions": True,
                            "real_pi": True,
                            "file_roundtrip": True,
                            "active_turn_survived": True,
                            "network_denied": True,
                            "drain_preserved_volume": True,
                            "stop_resume_transcript": True,
                            "sweep": sweep,
                        }
                    )
                )

        asyncio.run(checks())
    except BaseException:
        for instance in instances:
            subprocess.run(
                ["docker", "inspect", "--format", "{{json .State}}", instance], env=env, check=False
            )
            subprocess.run(["docker", "logs", "--tail", "40", instance], env=env, check=False)
        subprocess.run(
            compose + ["logs", "--tail", "80", "sandbox-manager", "sandbox-gateway"],
            env=env,
            check=False,
        )
        raise
    finally:
        for instance in instances:
            subprocess.run(
                ["docker", "rm", "-f", instance], env=env, check=False, capture_output=True
            )
        command(compose + ["down", "--remove-orphans"])
        print("Test user workspace volumes preserved; no volume deletion was requested.")


if __name__ == "__main__":
    main()
