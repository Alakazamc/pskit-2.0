"""Fixed installed Pi emits the published Chat Completions wire policy to loopback."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.adapters.live.pi_rpc import PiRpcRunner, _provider_error_code
from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy


@pytest.mark.parametrize("message,code", [
    ("429 MODEL_GATEWAY_QUOTA_EXHAUSTED", "MODEL_GATEWAY_QUOTA_EXHAUSTED"),
    ("401 MODEL_GATEWAY_AUTH_FAILED", "MODEL_GATEWAY_AUTH_FAILED"),
    ("429 MODEL_GATEWAY_RATE_LIMITED", "MODEL_GATEWAY_RATE_LIMITED"),
    ("400 MODEL_GATEWAY_INVALID_REQUEST", "MODEL_GATEWAY_INVALID_REQUEST"),
    ("418 UNKNOWN_UPSTREAM_ERROR", "PI_RUN_FAILED"),
    ("503 MODEL_GATEWAY_UNAVAILABLE", "PI_RUN_FAILED"),
])
def test_pi_prefers_sanitized_error_code_and_retains_ambiguous_holds(message, code):
    assert _provider_error_code(message) == code


@pytest.mark.asyncio
@pytest.mark.parametrize("level,wire", [("high", "high"), ("off", "none")])
async def test_real_pi_uses_profile_wire_fields_without_provider_specific_patches(tmp_path, level, wire):
    requests = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            chunks = []
            for delta, reason in (({"role": "assistant", "content": "Profile applied"}, None),
                                  ({}, "stop")):
                packet = {"id": "profile-reply", "object": "chat.completion.chunk",
                          "created": 1, "model": "lab-alias", "choices": [
                              {"index": 0, "delta": delta, "finish_reason": reason},
                          ]}
                if reason:
                    packet["usage"] = {"prompt_tokens": 8, "completion_tokens": 3,
                                       "total_tokens": 11}
                chunks.append(f"data: {json.dumps(packet)}\n\n".encode())
            body = b"".join(chunks) + b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    # CI permits isolated loopback; a missing socket/Pi is a real gate failure, not a skip.
    server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        profile = ModelRuntimeProfile(
            alias="lab-alias", revision=3, reasoning_levels=[level],
            max_output_tokens=128, context_window=32000,
            wire_policy=ModelWirePolicy(
                chat_max_tokens_field="max_completion_tokens", stream_usage_mode="not_supported",
                developer_role_mode="system_fallback", reasoning_mode="reasoning_effort",
            ),
        )
        runner = PiRpcRunner(
            executable=str(Path(__file__).resolve().parents[1] / "pi/node_modules/.bin/pi"),
            session_dir=str(tmp_path / "sessions"), timeout_seconds=30,
            model_gateway_base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model_gateway_model="lab-alias",
        )
        result = await runner.prompt("profile-session", "Synthetic test", lambda _event: None,
                                     environment={
                                         "MODEL_GATEWAY_API_KEY": "synthetic-loopback-key",
                                         "PSKIT_REASONING_EFFORT": level,
                                         "PSKIT_MODEL_RUNTIME_PROFILE": profile.model_dump_json(),
                                     })
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert result["text"] == "Profile applied"
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "lab-alias"
    assert 0 < request["max_completion_tokens"] <= 128
    assert "max_tokens" not in request and "stream_options" not in request
    assert request["reasoning_effort"] == wire
    assert all(message["role"] != "developer" for message in request["messages"])


@pytest.mark.asyncio
async def test_invalid_profile_fails_before_executable_is_started(tmp_path):
    runner = PiRpcRunner(
        executable="/does-not-exist", session_dir=str(tmp_path / "sessions"),
        model_gateway_base_url="http://internal.invalid", model_gateway_model="lab-alias",
    )
    from app.adapters.live.pi_rpc import PiRpcError

    with pytest.raises(PiRpcError) as caught:
        await runner.prompt("profile-session", "Synthetic test", lambda _event: None,
                            environment={"MODEL_GATEWAY_API_KEY": "synthetic-key",
                                         "PSKIT_MODEL_RUNTIME_PROFILE": '{"alias":"lab-alias"}'})
    assert caught.value.code == "MODEL_PROFILE_INVALID"
