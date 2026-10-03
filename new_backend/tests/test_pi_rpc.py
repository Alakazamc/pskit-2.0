
import json
from pathlib import Path

import pytest

from app.adapters.live.pi_rpc import PiRpcError, PiRpcRunner


@pytest.mark.asyncio
async def test_pi_rpc_sends_image_content_and_declares_vision_input(tmp_path):
    executable = tmp_path / "fake-pi-image"
    capture = tmp_path / "command.json"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
for line in sys.stdin:
    command = json.loads(line)
    if command['type'] == 'get_state':
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True,
                          'data': {'sessionFile': '/tmp/image-session.jsonl'}}), flush=True)
    elif command['type'] == 'prompt':
        pathlib.Path(os.environ['TEST_IMAGE_CAPTURE']).write_text(json.dumps(command))
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True,
                          'data': {'disposition': 'started'}}), flush=True)
        print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant',
                          'content': [{'type': 'text', 'text': 'seen'}]}}), flush=True)
        print(json.dumps({'type': 'agent_settled'}), flush=True)
        break
""", encoding="utf-8",
    )
    executable.chmod(0o755)
    runner = PiRpcRunner(
        executable=str(executable), session_dir=str(tmp_path / "sessions"),
        model_gateway_base_url="http://gateway:4000/v1", model_gateway_model="text-model",
    )
    images = [{"type": "image", "data": "cG5n", "mimeType": "image/png"}]
    result = await runner.prompt(
        "session-alice", "Describe", lambda _: None,
        images=images, environment={
            "TEST_IMAGE_CAPTURE": str(capture), "MODEL_GATEWAY_API_KEY": "test-key",
            "PSKIT_MODEL_ID": "vision-model", "PSKIT_MODEL_SUPPORTS_IMAGES": "1",
        },
    )
    config = json.loads((tmp_path / "sessions" / "session-alice" / ".pi-config"
                         / "models.json").read_text())
    assert json.loads(capture.read_text())["images"] == images
    assert config["providers"]["model-gateway"]["models"] == [
        {"id": "vision-model", "input": ["text", "image"]},
    ]
    assert result["text"] == "seen"


@pytest.mark.asyncio
async def test_pi_rpc_marks_early_process_exit_as_retryable(tmp_path):
    executable = tmp_path / "fake-pi-exit"
    executable.write_text("#!/usr/bin/env python3\nraise SystemExit(1)\n", encoding="utf-8")
    executable.chmod(0o755)
    runner = PiRpcRunner(executable=str(executable), session_dir=str(tmp_path / "sessions"))

    with pytest.raises(PiRpcError) as caught:
        await runner.prompt("session-alice", "Analyze", lambda _: None)

    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_pi_rpc_marks_deadline_exceeded_as_retryable(tmp_path):
    executable = tmp_path / "fake-pi-timeout"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time
for line in sys.stdin:
    command = json.loads(line)
    if command['type'] == 'get_state':
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True,
                          'data': {'sessionFile': '/tmp/pi-timeout.jsonl'}}), flush=True)
    elif command['type'] == 'prompt':
        time.sleep(5)
""", encoding="utf-8",
    )
    executable.chmod(0o755)
    runner = PiRpcRunner(executable=str(executable), session_dir=str(tmp_path / "sessions"),
                         timeout_seconds=0.1)

    with pytest.raises(PiRpcError, match="deadline exceeded") as caught:
        await runner.prompt("session-alice", "Analyze", lambda _: None)

    assert caught.value.retryable is True


@pytest.mark.asyncio
async def test_pi_rpc_failed_prompt_does_not_mutate_committed_session(tmp_path):
    committed = tmp_path / "committed.jsonl"
    committed.write_text("seed\n", encoding="utf-8")
    executable = tmp_path / "fake-pi-branch"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
session = pathlib.Path(sys.argv[sys.argv.index('--session') + 1])
assert session.read_text() == 'seed\\n'
for line in sys.stdin.buffer:
    command = json.loads(line)
    if command['type'] == 'get_state':
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True,
                          'data': {'sessionFile': str(session)}}), flush=True)
    elif command['type'] == 'prompt':
        session.write_text('seed\\n' + ('partial\\n' if os.getenv('TEST_FAIL') else 'complete\\n'))
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True,
                          'data': {'disposition': 'started'}}), flush=True)
        if os.getenv('TEST_FAIL'):
            print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant',
                              'stopReason': 'error', 'errorMessage': 'failed'}}), flush=True)
        else:
            print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant',
                              'content': [{'type': 'text', 'text': 'done'}]}}), flush=True)
        print(json.dumps({'type': 'agent_settled'}), flush=True)
        break
"""
    )
    executable.chmod(0o755)
    runner = PiRpcRunner(executable=str(executable), session_dir=str(tmp_path / "sessions"))

    with pytest.raises(PiRpcError):
        await runner.prompt("session-alice", "first", lambda _: None,
                            session_file=str(committed), environment={"TEST_FAIL": "1"})
    assert committed.read_text(encoding="utf-8") == "seed\n"
    result = await runner.prompt("session-alice", "second", lambda _: None,
                                 session_file=str(committed))
    assert result["text"] == "done"
    assert result["session_file"] != str(committed)
    assert committed.read_text(encoding="utf-8") == "seed\n"
    assert Path(result["session_file"]).read_text(encoding="utf-8") == "seed\ncomplete\n"


@pytest.mark.asyncio
async def test_pi_rpc_uses_generic_gateway_without_writing_secret_to_model_config(tmp_path):
    executable = tmp_path / "fake-pi-gateway"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
assert sys.argv[sys.argv.index('--provider') + 1] == 'model-gateway'
config = json.loads((pathlib.Path(os.environ['PI_CODING_AGENT_DIR']) / 'models.json').read_text())
provider = config['providers']['model-gateway']
assert provider['baseUrl'] == 'https://gateway.example/v1'
assert provider['apiKey'] == '$MODEL_GATEWAY_API_KEY'
assert os.environ['MODEL_GATEWAY_API_KEY'] == 'server-secret'
for line in sys.stdin.buffer:
    command = json.loads(line)
    if command['type'] == 'get_state':
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True, 'data': {'sessionFile': '/tmp/gateway-session.jsonl'}}), flush=True)
    elif command['type'] == 'prompt':
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True, 'data': {'disposition': 'started'}}), flush=True)
        print(json.dumps({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': '完成'}}), flush=True)
        print(json.dumps({'type': 'agent_settled'}), flush=True)
        break
"""
    )
    executable.chmod(0o755)
    runner = PiRpcRunner(
        executable=str(executable), session_dir=str(tmp_path / "sessions"),
        model_gateway_base_url="https://gateway.example/v1", model_gateway_model="research-model",
    )
    result = await runner.prompt(
        "session-alice", "你好", lambda _: None,
        environment={"MODEL_GATEWAY_API_KEY": "server-secret"},
    )
    config = json.loads((tmp_path / "sessions/session-alice/.pi-config/models.json").read_text())
    retry = json.loads((tmp_path / "sessions/session-alice/.pi-config/settings.json").read_text())["retry"]
    assert result["text"] == "完成"
    assert "server-secret" not in json.dumps(config)
    assert retry == {"enabled": True, "maxRetries": 3, "baseDelayMs": 2000,
                     "maxAgentDelayMs": 60000, "provider": {"maxRetries": 0}}


@pytest.mark.asyncio
async def test_pi_rpc_uses_selected_model_and_separate_session_configs(tmp_path):
    executable = tmp_path / "fake-pi-model"
    executable.write_text(
        """#!/usr/bin/env python3
import json, os, pathlib, sys
model = sys.argv[sys.argv.index('--model') + 1]
config = json.loads((pathlib.Path(os.environ['PI_CODING_AGENT_DIR']) / 'models.json').read_text())
assert config['providers']['model-gateway']['models'][0]['id'] == model
for line in sys.stdin.buffer:
 command = json.loads(line)
 if command['type'] == 'get_state':
  print(json.dumps({'id': command['id'], 'type': 'response', 'success': True,
                    'data': {'sessionFile': '/tmp/selected-model.jsonl'}}), flush=True)
 elif command['type'] == 'prompt':
  print(json.dumps({'id': command['id'], 'type': 'response', 'success': True,
                    'data': {'disposition': 'started'}}), flush=True)
  print(json.dumps({'type': 'message_end', 'message': {'role': 'assistant',
                    'content': [{'type': 'text', 'text': model}]}}), flush=True)
  print(json.dumps({'type': 'agent_settled'}), flush=True)
  break
""", encoding="utf-8")
    executable.chmod(0o755)
    runner = PiRpcRunner(
        executable=str(executable), session_dir=str(tmp_path / "sessions"),
        model_gateway_base_url="https://gateway.example/v1", model_gateway_model="default-model",
    )

    first = await runner.prompt("session-1", "hello", lambda _: None, environment={
        "MODEL_GATEWAY_API_KEY": "server-secret", "PSKIT_MODEL_ID": "chosen-model",
    })
    second = await runner.prompt("session-2", "hello", lambda _: None, environment={
        "MODEL_GATEWAY_API_KEY": "server-secret", "PSKIT_MODEL_ID": "default-model",
    })

    assert first["text"] == "chosen-model"
    assert second["text"] == "default-model"
    assert (tmp_path / "sessions/session-1/.pi-config/models.json").exists()
    assert (tmp_path / "sessions/session-2/.pi-config/models.json").exists()


@pytest.mark.asyncio
async def test_pi_rpc_routes_shared_key_through_server_owned_new_api_model_config(tmp_path):
    executable = tmp_path / "fake-pi-new-api"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys
assert sys.argv[sys.argv.index('--provider') + 1] == 'new-api'
assert sys.argv[sys.argv.index('--model') + 1] == 'research-model'
config = json.loads((pathlib.Path(os.environ['PI_CODING_AGENT_DIR']) / 'models.json').read_text())
provider = config['providers']['new-api']
assert provider['baseUrl'] == 'https://new-api.example/v1'
assert provider['apiKey'] == '$MODEL_GATEWAY_API_KEY'
assert os.environ['MODEL_GATEWAY_API_KEY'] == 'server-secret'
for line in sys.stdin.buffer:
    command = json.loads(line)
    if command['type'] == 'get_state':
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True, 'data': {'sessionFile': '/tmp/pi-session.jsonl'}}), flush=True)
    elif command['type'] == 'prompt':
        print(json.dumps({'id': command['id'], 'type': 'response', 'success': True, 'data': {'disposition': 'started'}}), flush=True)
        print(json.dumps({'type': 'message_update', 'assistantMessageEvent': {'type': 'text_delta', 'delta': '完成'}}), flush=True)
        print(json.dumps({'type': 'agent_settled'}), flush=True)
        break
"""
    )
    executable.chmod(0o755)
    runner = PiRpcRunner(
        executable=str(executable), session_dir=str(tmp_path / "sessions"),
        new_api_base_url="https://new-api.example", new_api_model="research-model",
    )
    result = await runner.prompt(
        "session-alice", "你好", lambda _: None,
        environment={"MODEL_GATEWAY_API_KEY": "server-secret"},
    )
    config = json.loads((tmp_path / "sessions/session-alice/.pi-config/models.json").read_text())
    assert result["text"] == "完成"
    assert "server-secret" not in json.dumps(config)


@pytest.mark.asyncio
async def test_pi_rpc_correlates_commands_and_waits_for_settlement(tmp_path):
    executable = tmp_path / "fake-pi"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys

assert "--mode" in sys.argv and "rpc" in sys.argv
assert "--no-builtin-tools" in sys.argv
for line in sys.stdin.buffer:
    command = json.loads(line)
    if command["type"] == "get_state":
        print(json.dumps({"id": command["id"], "type": "response", "command": "get_state", "success": True, "data": {"sessionFile": "/tmp/pi-session.jsonl"}}), flush=True)
    elif command["type"] == "prompt":
        print(json.dumps({"id": command["id"], "type": "response", "command": "prompt", "success": True, "data": {"disposition": "started"}}), flush=True)
        print(json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "你好\\u2028Pi"}}, ensure_ascii=False), flush=True)
        print(json.dumps({"type": "agent_settled"}), flush=True)
        break
"""
    )
    executable.chmod(0o755)
    observed = []
    runner = PiRpcRunner(executable=str(executable), session_dir=str(tmp_path / "sessions"))
    result = await runner.prompt("session-alice", "请分析", observed.append)

    assert result == {"session_file": "/tmp/pi-session.jsonl", "text": "你好\u2028Pi"}
    assert [event["type"] for event in observed] == ["message_update", "agent_settled"]


@pytest.mark.asyncio
async def test_pi_rpc_waits_for_agent_after_handled_extension_command(tmp_path):
    committed = tmp_path / "committed.jsonl"
    committed.write_text("seed\n", encoding="utf-8")
    executable = tmp_path / "fake-pi-resume"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
session = sys.argv[sys.argv.index('--session') + 1]
for line in sys.stdin.buffer:
    command = json.loads(line)
    if command["type"] == "get_state":
        print(json.dumps({"id": command["id"], "type": "response", "command": "get_state", "success": True, "data": {"sessionFile": session}}), flush=True)
    elif command["type"] == "prompt":
        print(json.dumps({"id": command["id"], "type": "response", "command": "prompt", "success": True, "data": {"disposition": "handled"}}), flush=True)
        print(json.dumps({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "已分析"}}), flush=True)
        print(json.dumps({"type": "agent_settled"}), flush=True)
        break
"""
    )
    executable.chmod(0o755)
    runner = PiRpcRunner(executable=str(executable), session_dir=str(tmp_path / "sessions"))
    result = await runner.prompt(
        "session-alice", "/pskit_resume job-1", lambda event: None,
        session_file=str(committed), allow_handled=True,
    )
    assert result["text"] == "已分析"
    assert result["session_file"] != str(committed)


@pytest.mark.asyncio
async def test_pi_rpc_provider_error_is_not_treated_as_an_answer(tmp_path):
    executable = tmp_path / "fake-pi-error"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import sys
for line in sys.stdin.buffer:
    command = json.loads(line)
    if command["type"] == "get_state":
        print(json.dumps({"id": command["id"], "type": "response", "command": "get_state", "success": True, "data": {"sessionFile": "/tmp/pi-session.jsonl"}}), flush=True)
    elif command["type"] == "prompt":
        print(json.dumps({"id": command["id"], "type": "response", "command": "prompt", "success": True, "data": {"disposition": "started"}}), flush=True)
        print(json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "errorMessage": "401 invalid API key", "content": [{"type": "text", "text": "bad API key"}]}}), flush=True)
        print(json.dumps({"type": "agent_settled"}), flush=True)
        break
"""
    )
    executable.chmod(0o755)
    runner = PiRpcRunner(executable=str(executable), session_dir=str(tmp_path / "sessions"))
    with pytest.raises(PiRpcError, match="provider error") as caught:
        await runner.prompt("session-alice", "你好", lambda event: None)
    assert caught.value.code == "MODEL_GATEWAY_AUTH_FAILED"
