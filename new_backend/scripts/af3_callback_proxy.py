"""Expose only AF3 compute callbacks through a loopback SSH tunnel.

The normal live API stays on its original loopback port. This small proxy
accepts one configured worker and only the HTTP methods/paths needed by the
AF3 receiver. The backend still validates its compute key and lease tokens.
"""

import argparse
import hmac
import http.client
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

JOB = r"[0-9a-fA-F-]{36}"
ARTIFACT = r"[A-Za-z0-9_-]{1,128}"
ROUTES = {
    ("POST", "/internal/compute/af3/jobs/claim"),
    ("GET", "/internal/compute/af3/jobs/owned"),
}
PATTERNS = {
    "POST": (
        re.compile(rf"/internal/compute/af3/jobs/{JOB}/(?:heartbeat|progress)\Z"),
        re.compile(rf"/internal/af3/jobs/{JOB}/result\Z"),
    ),
    "PUT": (re.compile(rf"/internal/af3/jobs/{JOB}/artifacts/{ARTIFACT}\Z"),),
}
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 1024 * 1024


def route_allowed(method: str, path: str) -> bool:
    """Allow only the methods and fixed AF3 callback route shapes."""
    return ((method, path) in ROUTES
            or any(pattern.fullmatch(path) for pattern in PATTERNS.get(method, ())))


def request_matches_worker(
    method: str, path: str, query: str, body: bytes, worker_id: str,
) -> bool:
    """Keep claim discovery and lease updates scoped to one A6000 worker."""
    if method == "GET" and path == "/internal/compute/af3/jobs/owned":
        return parse_qs(query) == {"worker_id": [worker_id]}
    if (method == "POST" and (
        path == "/internal/compute/af3/jobs/claim" or path.endswith(("/heartbeat", "/progress"))
    )):
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return False
        return isinstance(payload, dict) and payload.get("worker_id") == worker_id
    return True


def load_compute_key(path: Path) -> str:
    """Read only the key entry from the existing private backend env file."""
    values = [line.split("=", 1)[1] for line in path.read_text().splitlines()
              if line.startswith("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=")]
    if len(values) != 1 or len(values[0]) < 32:
        raise ValueError("One configured compute callback key is required")
    return values[0]


def make_handler(key: str, worker_id: str, upstream_port: int,
                 upstream_host: str = "127.0.0.1"):
    """Bind immutable proxy configuration into an HTTP request handler."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args) -> None:
            pass  # Never log compute credentials or artifact query strings.

        def do_GET(self) -> None:
            self._forward()

        def do_POST(self) -> None:
            self._forward()

        def do_PUT(self) -> None:
            self._forward()

        def _send(self, code: int, content: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _forward(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or not route_allowed(self.command, parsed.path):
                self._send(404, b'{"detail":"Not found"}')
                return
            supplied = self.headers.get("X-Compute-Key", "")
            if not hmac.compare_digest(supplied, key):
                self._send(404, b'{"detail":"Not found"}')
                return
            if self.headers.get("Transfer-Encoding"):
                self._send(411, b'{"detail":"Content-Length required"}')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, b'{"detail":"Invalid Content-Length"}')
                return
            maximum = MAX_ARTIFACT_BYTES if self.command == "PUT" else MAX_JSON_BYTES
            if length < 0 or length > maximum:
                self._send(413, b'{"detail":"Payload too large"}')
                return
            body = self.rfile.read(length) if length else b""
            if not request_matches_worker(self.command, parsed.path, parsed.query,
                                          body, worker_id):
                self._send(403, b'{"detail":"Worker not allowed"}')
                return
            upstream = http.client.HTTPConnection(upstream_host, upstream_port, timeout=30)
            try:
                headers = {"X-Compute-Key": supplied,
                           "Content-Type": self.headers.get("Content-Type", "application/json")}
                lease = self.headers.get("X-Compute-Lease")
                if lease:
                    headers["X-Compute-Lease"] = lease
                upstream.request(self.command, self.path, body=body, headers=headers)
                response = upstream.getresponse()
                content = response.read(2 * 1024 * 1024 + 1)
                if len(content) > 2 * 1024 * 1024:
                    self._send(502, b'{"detail":"Upstream response too large"}')
                    return
                self._send(response.status, content)
            except (OSError, http.client.HTTPException):
                self._send(502, b'{"detail":"Compute backend unavailable"}')
            finally:
                upstream.close()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="AF3-only loopback callback proxy")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--listen-port", type=int, default=18084)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, default=18080)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", args.worker_id):
        parser.error("Invalid worker ID")
    key = load_compute_key(args.env_file)
    server = ThreadingHTTPServer(
        (args.listen_host, args.listen_port),
        make_handler(key, args.worker_id, args.upstream_port, args.upstream_host),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
