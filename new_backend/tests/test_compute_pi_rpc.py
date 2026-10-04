"""Actual installed Pi RPC runtime; loopback model and tool endpoints are substitutes."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.adapters.live.pi_rpc import PiRpcRunner


@pytest.mark.asyncio
async def test_real_pi_suspends_compute_and_resumes_with_structured_terminal_result(tmp_path):
    model_requests: list[dict] = []
    submitted_jobs: list[dict] = []
    fetched_jobs: list[str] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):
            fetched_jobs.append(self.path)
            assert self.path == "/internal/compute/jobs/compute-1?run_id=run-1"
            body = json.dumps({"job": {"id": "compute-1", "status": "completed"},
                               "related": [{"id": "compute-1", "status": "completed", "report": {
                                   "result": {"score": 0.9}, "usage": {"gpu_device_ms": 7000, "source": "service_reported"}}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/internal/compute/jobs":
                submitted_jobs.append(payload)
                body = json.dumps({"id": "compute-1", "status": "queued"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            assert self.path == "/v1/chat/completions"
            model_requests.append(payload)
            if len(model_requests) == 1:
                chunks = [
                    ({"role": "assistant", "tool_calls": [{"index": 0, "id": "call-compute-1",
                      "type": "function", "function": {"name": "submit_compute",
                      "arguments": '{"capability_id":"lab.predict","version":"1","arguments":{"sequence":"ACG"},"budget":{"gpu_device_ms":10000}}'}}]}, None),
                    ({}, "tool_calls"),
                ]
            else:
                chunks = [({"role": "assistant", "content": "Compute result is ready"}, None),
                          ({}, "stop")]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, reason in chunks:
                packet = {"id": f"chatcmpl-{len(model_requests)}", "object": "chat.completion.chunk",
                          "created": 1, "model": "research-model", "choices": [{"index": 0,
                          "delta": delta, "finish_reason": reason}]}
                self.wfile.write(f"data: {json.dumps(packet)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *_):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    except PermissionError:
        pytest.skip("Loopback sockets are unavailable in the current sandbox")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = Path(__file__).resolve().parents[1]
        runner = PiRpcRunner(
            executable=str(root / "pi/node_modules/.bin/pi"),
            extension=str(root / "pi/extension.js"),
            session_dir=str(tmp_path / "pi-sessions"),
            model_gateway_base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model_gateway_model="research-model", timeout_seconds=30,
        )
        environment = {
            "MODEL_GATEWAY_API_KEY": "server-only-key", "PSKIT_RUN_ID": "run-1",
            "PSKIT_AGENT_TOOL_TOKEN": "internal-secret",
            "PSKIT_INTERNAL_API_URL": f"http://127.0.0.1:{server.server_port}",
            "PSKIT_AF3_ENABLED": "0", "PSKIT_MCP_TOOLS_JSON": "[]",
            "PSKIT_COMPUTE_CAPABILITIES_JSON": json.dumps([{
                "id": "lab.predict", "version": "1", "input_schema": {"type": "object"},
            }]),
        }
        first_events: list[dict] = []
        first = await runner.prompt(
            "session-1", "Run computation", first_events.append, environment=environment,
        )
        assert len(model_requests) == 1
        resumed_events: list[dict] = []
        resumed = await runner.prompt(
            "session-1", "/pskit_resume compute-1", resumed_events.append,
            session_file=first["session_file"], environment=environment, allow_handled=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert submitted_jobs == [{"run_id": "run-1", "tool_call_id": "call-compute-1",
                               "capability_id": "lab.predict", "version": "1",
                               "arguments": {"sequence": "ACG"}, "budget": {"gpu_device_ms": 10000}}]
    assert fetched_jobs == ["/internal/compute/jobs/compute-1?run_id=run-1"]
    assert len(model_requests) == 2
    assert resumed["text"] == "Compute result is ready"
    assert any(event.get("type") == "tool_execution_end" and
               (event.get("result") or {}).get("details", {}).get("status") == "pending"
               for event in first_events)

    assert any("7000" in json.dumps(message) and "service_reported" in json.dumps(message)
               for message in model_requests[-1]["messages"])

