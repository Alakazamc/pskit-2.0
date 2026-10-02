import http.client
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.af3_callback_proxy import make_handler, request_matches_worker, route_allowed


def test_proxy_uses_configured_upstream_host(monkeypatch):
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            seen.append((self.path, self.headers.get("X-Compute-Key"), self.rfile.read(
                int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        "secret", "worker-1", upstream.server_port, upstream_host="backend",
    ))
    original_getaddrinfo = socket.getaddrinfo

    def resolve_backend(host, *args, **kwargs):
        if host == "backend":
            host = "127.0.0.1"
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve_backend)
    threads = [threading.Thread(target=server.serve_forever, daemon=True)
               for server in (upstream, proxy)]
    for thread in threads:
        thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", proxy.server_port)
        connection.request("POST", "/internal/compute/af3/jobs/claim",
                           body=b'{"worker_id":"worker-1"}',
                           headers={"X-Compute-Key": "secret"})
        response = connection.getresponse()
        assert response.status == 200
        assert response.read() == b'{"ok":true}'
        assert seen == [("/internal/compute/af3/jobs/claim", "secret",
                         b'{"worker_id":"worker-1"}')]
        connection.close()
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()


def test_proxy_rejects_wrong_worker_key_path_and_oversized_body():
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        "secret", "worker-1", 1,
    ))
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        cases = [
            ("POST", "/internal/compute/af3/jobs/claim", b'{"worker_id":"worker-1"}',
             "wrong", 404),
            ("POST", "/internal/compute/af3/jobs/claim", b'{"worker_id":"other"}',
             "secret", 403),
            ("GET", "/api/v1/projects", b"", "secret", 404),
            ("POST", "/internal/compute/af3/jobs/claim", b"x" * (1024 * 1024 + 1),
             "secret", 413),
        ]
        for method, path, body, key, expected in cases:
            connection = http.client.HTTPConnection("127.0.0.1", proxy.server_port)
            connection.request(method, path, body=body, headers={"X-Compute-Key": key})
            response = connection.getresponse()
            assert response.status == expected
            response.read()
            connection.close()
    finally:
        proxy.shutdown()
        proxy.server_close()


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/internal/compute/af3/jobs/claim"),
    ("GET", "/internal/compute/af3/jobs/owned"),
    ("POST", "/internal/compute/af3/jobs/12345678-1234-1234-1234-123456789abc/heartbeat"),
    ("POST", "/internal/compute/af3/jobs/12345678-1234-1234-1234-123456789abc/progress"),
    ("PUT", "/internal/af3/jobs/12345678-1234-1234-1234-123456789abc/artifacts/model"),
    ("POST", "/internal/af3/jobs/12345678-1234-1234-1234-123456789abc/result"),
])
def test_proxy_allows_only_needed_compute_routes(method, path):
    assert route_allowed(method, path)


@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/v1/projects"),
    ("GET", "/internal/metrics"),
    ("GET", "/health/ready"),
    ("GET", "/internal/compute/af3/jobs/12345678-1234-1234-1234-123456789abc"),
    ("DELETE", "/internal/compute/af3/jobs/claim"),
    ("POST", "/internal/af3/jobs/../../api/v1/projects/result"),
    ("POST", "/internal/compute/af3/jobs/claim/extra"),
    ("POST", "/internal/compute/af3/jobs/%2e%2e/heartbeat"),
])
def test_proxy_rejects_other_routes(method, path):
    assert not route_allowed(method, path)


def test_proxy_limits_claims_and_heartbeats_to_one_worker():
    worker = "a6000-receiver-test-20261002"
    assert request_matches_worker("GET", "/internal/compute/af3/jobs/owned",
                                  f"worker_id={worker}", b"", worker)
    assert not request_matches_worker("GET", "/internal/compute/af3/jobs/owned",
                                      "worker_id=other", b"", worker)
    assert request_matches_worker("POST", "/internal/compute/af3/jobs/claim", "",
                                  b'{"worker_id":"a6000-receiver-test-20261002"}', worker)
    assert not request_matches_worker("POST", "/internal/compute/af3/jobs/claim", "",
                                      b'{"worker_id":"other"}', worker)
