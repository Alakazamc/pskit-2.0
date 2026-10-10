import base64
import json
import random
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.adapters.live.pi_rpc import PiRpcError, PiRpcRunner, _provider_error_code


def test_local_proxy_quota_error_is_not_classified_as_gateway_rate_limit():
    assert _provider_error_code("HTTP 402 PSKIT_TOKEN_QUOTA_EXCEEDED") == "TOKEN_QUOTA_EXCEEDED"


@pytest.mark.asyncio
@pytest.mark.parametrize("image_count", [1, 2, 10])
async def test_real_pi_image_events_larger_than_default_pipe_limit_reach_the_gateway(
    tmp_path, image_count,
):
    """Pi echoes image bytes in user events before the first assistant event."""
    def chunk(kind, raw):
        return struct.pack("!I", len(raw)) + kind + raw + struct.pack(
            "!I", zlib.crc32(kind + raw) & 0xFFFFFFFF,
        )

    pixels = random.Random(0).randbytes(256 * 256 * 3)
    scanlines = b"".join(b"\x00" + pixels[offset:offset + 768]
                         for offset in range(0, len(pixels), 768))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack("!2I5B", 256, 256, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))
    encoded = base64.b64encode(png).decode()
    assert len(encoded) > 65536
    requests = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            packets = []
            for delta, reason in (({"role": "assistant", "content": "Image reviewed"}, None),
                                  ({}, "stop")):
                packet = {"id": "image-reply", "object": "chat.completion.chunk",
                          "created": 1, "model": "vision-model", "choices": [
                              {"index": 0, "delta": delta, "finish_reason": reason},
                          ]}
                if reason:
                    packet["usage"] = {"prompt_tokens": 8, "completion_tokens": 3,
                                       "total_tokens": 11}
                packets.append(f"data: {json.dumps(packet)}\n\n".encode())
            body = b"".join(packets) + b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
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
        runner = PiRpcRunner(
            executable=str(Path(__file__).resolve().parents[1] / "pi/node_modules/.bin/pi"),
            session_dir=str(tmp_path / "sessions"),
            model_gateway_base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model_gateway_model="vision-model", timeout_seconds=30,
        )
        events = []
        try:
            result = await runner.prompt("image-session", "Describe this image", events.append,
                                         images=[{"type": "image", "mimeType": "image/png",
                                                  "data": encoded}] * image_count, environment={
                                             "MODEL_GATEWAY_API_KEY": "test-only-key",
                                             "PSKIT_MODEL_SUPPORTS_IMAGES": "1",
                                         })
        except PiRpcError as exc:
            raise AssertionError({"requests": len(requests), "events": [
                {"type": event.get("type"),
                 "error": event.get("message", {}).get("errorMessage")}
                for event in events
            ]}) from exc
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result["text"] == "Image reviewed"
    user_event = next(event for event in events if event.get("type") == "message_start"
                      and event.get("message", {}).get("role") == "user")
    assert len([block for block in user_event["message"]["content"]
                if block.get("data") == encoded]) == image_count
    content = next(message["content"] for message in requests[0]["messages"]
                   if message["role"] == "user")
    assert len([block for block in content
                if block.get("image_url", {}).get("url") == f"data:image/png;base64,{encoded}"]
               ) == image_count


@pytest.mark.asyncio
async def test_real_pi_streams_from_local_openai_compatible_gateway(tmp_path):
    requests: list[dict] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                             "body": body})
            assert self.path == "/v1/chat/completions"
            payloads = [
                {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1,
                 "model": "research-model", "choices": [{"index": 0,
                 "delta": {"role": "assistant", "content": "本地网关已连接"}, "finish_reason": None}]},
                {"id": "chatcmpl-test", "object": "chat.completion.chunk", "created": 1,
                 "model": "research-model", "choices": [{"index": 0, "delta": {},
                 "finish_reason": "stop"}], "usage": {"prompt_tokens": 8,
                 "completion_tokens": 5, "total_tokens": 13}},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for payload in payloads:
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
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
        pi = Path(__file__).resolve().parents[1] / "pi/node_modules/.bin/pi"
        runner = PiRpcRunner(
            executable=str(pi), session_dir=str(tmp_path / "pi-sessions"),
            model_gateway_base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model_gateway_model="research-model", timeout_seconds=30,
        )
        events: list[dict] = []
        result = await runner.prompt(
            "session-1", "回应网关联通测试", events.append,
            environment={"MODEL_GATEWAY_API_KEY": "server-only-key"},
        )
        first_file = Path(result["session_file"])
        first_contents = first_file.read_bytes()
        resumed = await runner.prompt(
            "session-1", "再回应一次", events.append,
            session_file=result["session_file"],
            environment={"MODEL_GATEWAY_API_KEY": "server-only-key"},
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert result["text"] == "本地网关已连接"
    assert resumed["text"] == "本地网关已连接"
    assert resumed["session_file"] != result["session_file"]
    assert Path(resumed["session_file"]).exists()
    assert first_file.read_bytes() == first_contents
    assert requests[0]["auth"] == "Bearer server-only-key"
    assert requests[0]["body"]["model"] == "research-model"
    assert any(event.get("type") == "message_end" for event in events)
    assert any(event.get("message", {}).get("usage", {}).get("totalTokens") == 13
               for event in events if event.get("type") == "message_end")


@pytest.mark.asyncio
async def test_real_pi_calls_mcp_extension_tool_then_continues_through_local_gateway(tmp_path):
    model_requests: list[dict] = []
    tool_requests: list[dict] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/internal/mcp/tools/search_pdb/invoke":
                tool_requests.append({"body": payload, "auth": self.headers.get("Authorization")})
                body = json.dumps({"status": "completed", "result": {"pdb_id": "1ABC"}}).encode()
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
                    {"role": "assistant", "tool_calls": [{"index": 0, "id": "call-search-1",
                     "type": "function", "function": {"name": "search_pdb",
                     "arguments": '{"query":"1ABC"}'}}]},
                    {},
                ]
                finish_reasons = [None, "tool_calls"]
            else:
                chunks = [{"role": "assistant", "content": "Found PDB 1ABC"}, {}]
                finish_reasons = [None, "stop"]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, reason in zip(chunks, finish_reasons, strict=True):
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
        events: list[dict] = []
        result = await runner.prompt(
            "session-1", "Search PDB for 1ABC", events.append,
            environment={
                "MODEL_GATEWAY_API_KEY": "server-only-key",
                "PSKIT_RUN_ID": "run-1", "PSKIT_AGENT_TOOL_TOKEN": "internal-secret",
                "PSKIT_INTERNAL_API_URL": f"http://127.0.0.1:{server.server_port}",
                "PSKIT_AF3_ENABLED": "0",
                "PSKIT_MCP_TOOLS_JSON": json.dumps([{
                    "name": "search_pdb", "description": "Search PDB structures",
                    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}},
                                     "required": ["query"], "additionalProperties": False},
                }]),
            },
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert result["text"] == "Found PDB 1ABC"
    assert len(model_requests) == 2
    assert model_requests[1]["messages"][-1]["role"] == "tool"
    assert tool_requests == [{"body": {"run_id": "run-1", "tool_call_id": "call-search-1", "arguments": {"query": "1ABC"}},
                              "auth": "Bearer internal-secret"}]
    assert any(event.get("type") == "tool_execution_start" and event.get("toolName") == "search_pdb"
               for event in events)


@pytest.mark.asyncio
async def test_real_pi_suspends_af3_tool_and_resumes_from_its_session(tmp_path):
    model_requests: list[dict] = []
    submitted_jobs: list[dict] = []
    fetched_jobs: list[str] = []

    class Gateway(BaseHTTPRequestHandler):
        def do_GET(self):
            fetched_jobs.append(self.path)
            assert self.path == "/internal/af3/jobs/job-1?run_id=run-1"
            body = json.dumps({"id": "job-1", "status": "completed", "simulation": True,
                               "artifacts": [{"id": "artifact-1", "name": "model.cif"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/internal/af3/jobs":
                submitted_jobs.append(payload)
                body = json.dumps({"id": "job-1", "status": "queued"}).encode()
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
                    ({"role": "assistant", "tool_calls": [{"index": 0, "id": "call-af3-1",
                      "type": "function", "function": {"name": "submit_af3",
                      "arguments": '{"estimated_gpu_minutes":5}'}}]}, None),
                    ({}, "tool_calls"),
                ]
            else:
                chunks = [({"role": "assistant", "content": "AF3 result is ready"}, None),
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
            "PSKIT_AF3_ENABLED": "1", "PSKIT_MCP_TOOLS_JSON": "[]",
        }
        first_events: list[dict] = []
        first = await runner.prompt(
            "session-1", "Run AF3", first_events.append, environment=environment,
        )
        assert len(model_requests) == 1
        resumed_events: list[dict] = []
        resumed = await runner.prompt(
            "session-1", "/pskit_resume job-1", resumed_events.append,
            session_file=first["session_file"], environment=environment, allow_handled=True,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert submitted_jobs == [{"run_id": "run-1", "tool_call_id": "call-af3-1",
                               "estimated_gpu_minutes": 5}]
    assert fetched_jobs == ["/internal/af3/jobs/job-1?run_id=run-1"]
    assert len(model_requests) == 2
    assert resumed["text"] == "AF3 result is ready"
    assert any(event.get("type") == "tool_execution_end" and
               (event.get("result") or {}).get("details", {}).get("status") == "pending"
               for event in first_events)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_code,http_status,retry", [
    ("rate_limit_exceeded", 429, True), ("insufficient_quota", 429, False),
    ("PSKIT_TOKEN_QUOTA_EXCEEDED", 402, False),
])
async def test_real_pi_retries_transient_429_but_not_budget_429(
    tmp_path, error_code, http_status, retry,
):
    requests = 0

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal requests
            requests += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            if requests == 1 or not retry:
                body = json.dumps({"error": {"message": error_code, "type": error_code,
                                              "code": error_code}}).encode()
                self.send_response(http_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for payload in [
                {"id": "chatcmpl-retry", "object": "chat.completion.chunk", "created": 1,
                 "model": "research-model", "choices": [{"index": 0,
                 "delta": {"role": "assistant", "content": "重试成功"}, "finish_reason": None}]},
                {"id": "chatcmpl-retry", "object": "chat.completion.chunk", "created": 1,
                 "model": "research-model", "choices": [{"index": 0, "delta": {},
                 "finish_reason": "stop"}]},
            ]:
                self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *_):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    except PermissionError:
        pytest.skip("Loopback sockets are unavailable in the current sandbox")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        pi = Path(__file__).resolve().parents[1] / "pi/node_modules/.bin/pi"
        runner = PiRpcRunner(
            executable=str(pi), session_dir=str(tmp_path / "pi-sessions"),
            model_gateway_base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model_gateway_model="research-model", timeout_seconds=30,
        )
        events: list[dict] = []
        if retry:
            result = await runner.prompt(
                "session-1", "测试临时限流", events.append,
                environment={"MODEL_GATEWAY_API_KEY": "server-only-key"},
            )
            assert result["text"] == "重试成功"
            assert requests == 2
            assert any(event.get("type") == "auto_retry_start" for event in events)
        else:
            with pytest.raises(PiRpcError) as caught:
                await runner.prompt(
                    "session-1", "测试额度耗尽", events.append,
                    environment={"MODEL_GATEWAY_API_KEY": "server-only-key"},
                )
            assert requests == 1
            assert caught.value.code == (
                "TOKEN_QUOTA_EXCEEDED" if error_code.startswith("PSKIT_")
                else "MODEL_GATEWAY_QUOTA_EXHAUSTED"
            )
            retry_errors = [event.get("errorMessage") for event in events
                            if event.get("type") == "auto_retry_start"]
            assert not retry_errors, retry_errors
            assert any(
                error_code in str(event.get("message", {}).get("errorMessage", ""))
                for event in events if event.get("type") == "message_end"
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
