"""The split cloud Web must start without a backend container."""

from contextlib import contextmanager
from pathlib import Path
import subprocess
import time
import uuid

import httpx


ROOT = Path(__file__).resolve().parents[3]
IMAGE = "pskit-agent-web:split-test"
PYTHON_IMAGE = (
    "python:3.12.12-slim-bookworm@"
    "sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c"
)
AUTH_UPSTREAM = """
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        body = b'oauth'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
ThreadingHTTPServer(('0.0.0.0', 8000), Handler).serve_forever()
"""


def docker(*arguments: str) -> str:
    result = subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


@contextmanager
def static_web():
    docker("build", "-f", "deploy/agent/web.Dockerfile", "--build-arg",
           "WEB_NGINX_CONFIG=deploy/agent/web.static.conf", "-t", IMAGE, ".")
    suffix = uuid.uuid4().hex[:12]
    network = f"pskit-split-web-{suffix}"
    upstream = f"pskit-split-auth-{suffix}"
    web = f"pskit-split-web-{suffix}"
    docker("network", "create", network)
    try:
        docker("run", "--rm", "--detach", "--name", upstream,
               "--network", network, "--network-alias", "api-gw",
               PYTHON_IMAGE, "python", "-u", "-c", AUTH_UPSTREAM)
        docker("run", "--rm", "--detach", "--name", web,
               "--network", network, "-p", "127.0.0.1::80", IMAGE)
        url = f"http://{docker('port', web, '80/tcp')}"
        for _ in range(40):
            try:
                if httpx.get(f"{url}/session/x", timeout=1).status_code == 200:
                    break
            except httpx.RequestError:
                pass
            time.sleep(0.25)
        else:
            raise AssertionError("static Web did not start without backend DNS")
        yield url
    finally:
        subprocess.run(["docker", "rm", "-f", web, upstream], capture_output=True)
        subprocess.run(["docker", "network", "rm", network], capture_output=True)


def test_static_web_starts_without_backend_dns():
    assert (ROOT / "deploy/agent/web.static.conf").exists(), (
        "static Web configuration is required"
    )
    with static_web() as static_web_url:
        page = httpx.get(f"{static_web_url}/session/x")
        assert page.status_code == 200
        assert "text/html" in page.headers["content-type"]
        authorize = httpx.get(f"{static_web_url}/auth/v1/authorize")
        assert authorize.status_code == 200
        assert authorize.text == "oauth"
        assert httpx.get(f"{static_web_url}/api/v1/ping").status_code == 404
        assert httpx.get(f"{static_web_url}/internal/x").status_code == 404
