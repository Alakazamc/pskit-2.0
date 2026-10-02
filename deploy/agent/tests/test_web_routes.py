"""Exercise the built web gateway against isolated Docker upstreams."""

import os
import subprocess
import time
import uuid

import httpx
import pytest


IMAGE = os.getenv("PSKIT_WEB_IMAGE", "pskit-agent-web:local")
PYTHON_IMAGE = (
    "python:3.12.12-slim-bookworm@"
    "sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c"
)
UPSTREAM = r'''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path == "/api/v1/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: first\n\n")
            self.wfile.flush()
            time.sleep(1.2)
            self.wfile.write(b"data: second\n\n")
            self.wfile.flush()
            return
        body = b"oauth" if self.path.startswith("/auth/v1/") else b"backend"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
'''


def docker(*arguments: str) -> str:
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture(scope="module")
def web_url():
    docker("image", "inspect", IMAGE)
    suffix = uuid.uuid4().hex[:12]
    network = f"pskit-web-contract-{suffix}"
    upstream = f"pskit-web-upstream-{suffix}"
    web = f"pskit-web-contract-{suffix}"
    docker("network", "create", network)
    try:
        docker("run", "--rm", "--detach", "--name", upstream,
               "--network", network, "--network-alias", "backend",
               "--network-alias", "api-gw", PYTHON_IMAGE,
               "python", "-u", "-c", UPSTREAM)
        docker("run", "--rm", "--detach", "--name", web,
               "--network", network, "-p", "127.0.0.1::80", IMAGE)
        address = docker("port", web, "80/tcp")
        url = f"http://{address}"
        for _ in range(40):
            try:
                if httpx.get(url, timeout=1).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(0.25)
        else:
            raise AssertionError("web image did not become ready")
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", web, upstream], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


def test_web_spa_and_api_routes(web_url):
    assert httpx.get(f"{web_url}/session/some-session").status_code == 200
    response = httpx.get(f"{web_url}/api/v1/ping")
    assert response.status_code == 200
    assert response.text == "backend"


def test_web_stream_and_upload(web_url):
    started = time.monotonic()
    with httpx.stream("GET", f"{web_url}/api/v1/stream", timeout=4) as response:
        assert response.status_code == 200
        events = response.iter_lines()
        assert next(events) == "data: first"
        assert time.monotonic() - started < 1.0
        assert list(events)[-2:] == ["data: second", ""]
    payload = b"research-upload\x00example"
    response = httpx.post(f"{web_url}/api/v1/upload", content=payload)
    assert response.status_code == 200
    assert response.content == payload


def test_web_rejects_private_routes(web_url):
    for path in ("/internal/compute/af3/jobs/claim", "/auth/v1/user", "/auth/v1/admin/users"):
        assert httpx.get(f"{web_url}{path}").status_code == 404
    for path in ("/auth/v1/authorize", "/auth/v1/callback"):
        response = httpx.get(f"{web_url}{path}")
        assert response.status_code == 200
        assert response.text == "oauth"
