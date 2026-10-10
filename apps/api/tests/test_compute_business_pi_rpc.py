"""Installed Pi exercises synthetic business schemas and same-session continuation."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.adapters.live.pi_rpc import PiRpcRunner


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "cancelled"])
async def test_real_pi_business_tool_suspends_then_resumes_same_session(tmp_path, terminal_status):
    tool_name = "pskit_document_echo_849df9"
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"], "additionalProperties": False}
    model_requests, submissions, fetched = [], [], []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):
            fetched.append(self.path)
            assert self.path == "/internal/compute/jobs/compute-document?run_id=run-document"
            report = {"result": {"text": "echoed document"}, "usage": {
                "cpu_core_ms": 12, "source": "service_reported"}}
            job = {"id": "compute-document", "status": terminal_status, "report": report}
            self.reply({"job": job, "related": [job]})

        def reply(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == f"/internal/compute/tools/{tool_name}/jobs":
                submissions.append(payload)
                self.reply({"id": "compute-document", "status": "queued"})
                return
            assert self.path == "/v1/chat/completions"
            model_requests.append(payload)
            if len(model_requests) == 1:
                chunks = [({"role": "assistant", "tool_calls": [{"index": 0,
                    "id": "call-document", "type": "function", "function": {"name": tool_name,
                    "arguments": '{"text":"echoed document"}'}}]}, None), ({}, "tool_calls")]
            else:
                chunks = [({"role": "assistant", "content": f"Document job {terminal_status}"}, None),
                          ({}, "stop")]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, reason in chunks:
                packet = {"id": f"synthetic-{len(model_requests)}", "object": "chat.completion.chunk",
                          "created": 1, "model": "document-model", "choices": [{"index": 0,
                          "delta": delta, "finish_reason": reason}]}
                self.wfile.write(f"data: {json.dumps(packet)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *_):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    except PermissionError:
        pytest.skip("Loopback sockets are unavailable in this sandbox")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = Path(__file__).resolve().parents[1]
        runner = PiRpcRunner(
            executable=str(root / "pi/node_modules/.bin/pi"), extension=str(root / "pi/extension.js"),
            session_dir=str(tmp_path / "pi-sessions"),
            model_gateway_base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model_gateway_model="document-model", timeout_seconds=30,
        )
        environment = {
            "MODEL_GATEWAY_API_KEY": "synthetic-model-key", "PSKIT_RUN_ID": "run-document",
            "PSKIT_AGENT_TOOL_TOKEN": "synthetic-tool-token",
            "PSKIT_INTERNAL_API_URL": f"http://127.0.0.1:{server.server_port}",
            "PSKIT_AF3_ENABLED": "0", "PSKIT_MCP_TOOLS_JSON": "[]",
            "PSKIT_COMPUTE_CAPABILITIES_JSON": "[]",
            "PSKIT_COMPUTE_TOOLS_JSON": json.dumps([{
                "name": tool_name, "description": "Echo a synthetic document",
                "input_schema": input_schema, "release_id": "release-documents-7", "action_id": "echo",
            }]),
        }
        first_events = []
        first = await runner.prompt("document-session", "Echo this document", first_events.append,
                                    environment=environment)
        assert len(model_requests) == 1
        committed_transcript = Path(first["session_file"]).read_bytes()
        resumed_events = []
        resumed = await runner.prompt(
            "document-session", "/pskit_resume compute-document", resumed_events.append,
            session_file=first["session_file"], environment=environment, allow_handled=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    tool = next(item["function"] for item in model_requests[0]["tools"]
                if item["function"]["name"] == tool_name)
    assert tool["parameters"] == input_schema
    assert "submit_compute" not in {item["function"]["name"] for item in model_requests[0]["tools"]}
    assert submissions == [{"run_id": "run-document", "tool_call_id": "call-document",
                            "release_id": "release-documents-7", "action_id": "echo",
                            "arguments": {"text": "echoed document"}}]
    assert fetched == ["/internal/compute/jobs/compute-document?run_id=run-document"]
    assert len(model_requests) == 2
    assert resumed["text"] == f"Document job {terminal_status}"
    # Attempts have separate files; continuation must retain the committed Pi
    # session identity/history and leave the previously committed file intact.
    assert Path(first["session_file"]).read_bytes() == committed_transcript
    resumed_transcript = Path(resumed["session_file"]).read_bytes()
    assert resumed_transcript.startswith(committed_transcript)
    original_session = json.loads(committed_transcript.splitlines()[0])
    resumed_session = json.loads(resumed_transcript.splitlines()[0])
    assert original_session["id"] == resumed_session["id"]
    assert any(event.get("type") == "tool_execution_end" and
               (event.get("result") or {}).get("details", {}).get("status") == "pending"
               for event in first_events)
    assert any(terminal_status in json.dumps(message) and "service_reported" in json.dumps(message)
               for message in model_requests[-1]["messages"])
