"""Public tool summaries must not derive visible text from untrusted payloads."""

from copy import deepcopy

import pytest

from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.agent_events import project_pi_event

UNTRUSTED_TEXT = [
    pytest.param("Authorization: Bearer private-token", id="bearer"),
    pytest.param("sk-provider-secret-0123456789abcdef", id="provider-key"),
    pytest.param("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwcml2YXRlIn0.private-signature", id="jwt"),
    pytest.param('{"password":"private-password"}', id="json-quoted-password"),
    pytest.param('{"Authorization":"Basic cHJpdmF0ZTpzZWNyZXQ="}', id="json-basic"),
    pytest.param("Authorization: Basic cHJpdmF0ZTpzZWNyZXQ=", id="basic"),
    pytest.param("https://provider.invalid/job?access_token=private-token", id="query-token"),
    pytest.param("https://private-user:private-password@provider.invalid/job", id="url-password"),
    pytest.param("/workspace/private-study/patient-input.csv", id="private-path"),
    pytest.param("private-line-one\nprivate-line-two\r\nprivate-line-three", id="multiline"),
    pytest.param("\x1b[31mprivate-error\x1b[0m", id="ansi"),
    pytest.param("private\x00\x01\x08\x1b]52;c;Y3JlZGVudGlhbA==\x07", id="control"),
    pytest.param("private-binary:" + "\x00\xff" * 50000, id="large-binary"),
    pytest.param("private-log:" + "unreported output " * 10000, id="large-log"),
    pytest.param("unpublished protein=MKWVTFISLLFLFSSAYS RNA=AGCUAGCUUAGCU", id="scientific-input"),
]


def _event(result, phase):
    event = {
        "type": "tool_execution_update" if phase == "progress" else "tool_execution_end",
        "toolCallId": "call-1",
        "toolName": "bash",
    }
    event["partialResult" if phase == "progress" else "result"] = result
    if phase == "failure":
        event["isError"] = True
    return event


@pytest.mark.parametrize("phase,expected", [("progress", "Tool running"), ("failure", "Tool failed")])
@pytest.mark.parametrize("text", UNTRUSTED_TEXT)
@pytest.mark.parametrize("location", ["content", "details"])
def test_untrusted_text_never_becomes_a_public_tool_summary(phase, expected, text, location):
    result = ({"content": [{"type": "text", "text": text}]} if location == "content" else
              {"details": {"message": text, "error": text}})
    original = deepcopy(result)

    projected = project_pi_event("run-1", _event(result, phase))

    assert len(projected) == 1
    assert projected[0].type == ("tool.updated" if phase == "progress" else "tool.finished")
    assert projected[0].data.summary == expected
    if phase == "failure":
        assert projected[0].data.status == "failed"
    assert result == original  # The model's tool result remains available to its existing consumer.


@pytest.mark.parametrize("code,expected", [
    ("WORKSPACE_UNAVAILABLE", "Workspace is unavailable"),
    ("WORKSPACE_TOKEN_INVALID", "Workspace authorization failed"),
    ("WORKSPACE_ACCESS_DISABLED", "Workspace access is disabled"),
    ("WORKSPACE_SCOPE_MISMATCH", "Workspace access was denied"),
    ("WORKSPACE_RUN_NOT_FOUND", "Workspace run was not found"),
    ("WORKSPACE_RUN_NOT_ACTIVE", "Workspace run is no longer active"),
    ("WORKSPACE_ATTEMPT_NOT_ACTIVE", "Workspace attempt is no longer active"),
    ("WORKSPACE_FILE_ACCESS_UNAVAILABLE", "Workspace file access is unavailable"),
    ("WORKSPACE_COMMANDS_DISABLED", "Workspace commands are disabled"),
    ("WORKSPACE_TEXT_DECODE_FAILED", "Workspace file is not valid UTF-8 text"),
    ("WORKSPACE_CONTENT_INVALID", "Workspace content is invalid"),
    ("WORKSPACE_CANCEL_UNCONFIRMED", "Workspace cancellation could not be confirmed"),
    ("WORKSPACE_UNSAFE_RUNTIME", "Workspace isolation could not be verified"),
    ("WORKSPACE_CONFLICT", "Workspace state changed; retry the operation"),
    ("WORKSPACE_TIMEOUT", "Workspace operation timed out"),
    ("WORKSPACE_CAPACITY", "Workspace capacity is unavailable"),
    ("WORKSPACE_NOT_FOUND", "Workspace resource was not found"),
    ("WORKSPACE_INVALID_PATH", "Workspace path is not allowed"),
    ("CPU_QUOTA_EXCEEDED", "Daily CPU quota is exhausted"),
])
def test_allowlisted_error_code_selects_a_fixed_failure_category(code, expected):
    projected = project_pi_event("run-1", _event({
        "details": {"code": code, "message": "private provider response"},
        "content": [{"type": "text", "text": "private input"}],
    }, "failure"))

    assert projected[0].data.status == "failed"
    assert projected[0].data.summary == expected
    assert "private" not in projected[0].model_dump_json()


@pytest.mark.parametrize("code", [
    "WORKSPACE_UNAVAILABLE private-token", "workspace_unavailable",
    "WORKSPACE_UNAVAILABLE\nsecret", "private-provider-code", ["WORKSPACE_UNAVAILABLE"],
    {"code": "WORKSPACE_UNAVAILABLE"}, None, True,
])
def test_unknown_or_malformed_error_code_does_not_echo_or_select_a_category(code):
    projected = project_pi_event("run-1", _event({"details": {"code": code}}, "failure"))

    assert projected[0].data.status == "failed"
    assert projected[0].data.summary == "Tool failed"


@pytest.mark.parametrize("result", [
    None, "private raw result", {"content": [{"type": "text", "text": {"secret": "private"}}]},
    {"details": ["private"]}, {"content": "private"},
])
@pytest.mark.parametrize("phase,expected", [("progress", "Tool running"), ("failure", "Tool failed")])
def test_malformed_payload_cannot_crash_summary_projection(result, phase, expected):
    projected = project_pi_event("run-1", _event(result, phase))

    assert projected[0].data.summary == expected


@pytest.mark.parametrize("status,expected", [
    ("completed", "Tool completed"), ("pending", "Background task submitted"),
    ("approval_required", "Waiting for approval"), ("failed", "Tool failed"),
])
def test_safe_summary_preserves_existing_terminal_status_contract(status, expected):
    event = _event({"details": {"status": status, "code": "WORKSPACE_UNAVAILABLE"}}, "failure")
    event["isError"] = False

    projected = project_pi_event("run-1", event)

    assert projected[0].data.status == status
    assert projected[0].data.summary == ("Workspace is unavailable" if status == "failed" else expected)


@pytest.mark.parametrize("status", [["pending"], {"status": "pending"}, None, True, False, "unknown"])
@pytest.mark.parametrize("phase", ["progress", "failure"])
def test_malformed_status_on_non_error_result_keeps_default_lifecycle_mapping(status, phase):
    event = _event({"details": {"status": status}}, phase)
    event["isError"] = False

    projected = project_pi_event("run-1", event)

    assert len(projected) == 1
    if phase == "progress":
        assert projected[0].type == "tool.updated"
        assert projected[0].data.summary == "Tool running"
    else:
        assert projected[0].type == "tool.finished"
        assert projected[0].data.status == "completed"
        assert projected[0].data.summary == "Tool completed"


def test_command_exit_code_reports_category_without_echoing_stdout_or_stderr():
    projected = project_pi_event("run-1", _event({
        "details": {"status": "failed", "exit_code": 2},
        "content": [{"type": "text", "text": "private stderr"}],
    }, "failure"))

    assert projected[0].data.status == "failed"
    assert projected[0].data.summary == "Command exited with an error"


def test_projected_safe_summaries_remain_safe_in_persisted_events_and_messages(tmp_path):
    store = PersistentConversationStore(str(tmp_path / "agent.sqlite3"))
    accepted = store.accept_message("alice", "session-alice", MessageRequest(content="Run command"),
                                    None, 2, None, "hash")
    assert accepted is not None
    run_id = accepted[0].run_id
    result = {"content": [{"type": "text", "text": "private experimental data"}],
              "details": {"error": '{"secret":"private token"}'}}
    for phase in ("progress", "failure"):
        for projected in project_pi_event(run_id, _event(result, phase)):
            store.append_event("alice", run_id, projected)
    store.add_assistant_reply("alice", run_id, "Command failed")

    events = store.events_for("alice", run_id, None)
    messages = store.messages_for("alice", "session-alice")
    assert events is not None and messages is not None
    tool_events = [event for event in events if event.type in {"tool.updated", "tool.finished"}]
    assert [event.data.summary for event in tool_events] == ["Tool running", "Tool failed"]
    assert "private" not in "".join(event.model_dump_json() for event in events)
    assert "private" not in messages[-1].model_dump_json()
    tool_parts = [part for part in messages[-1].parts if part.type == "tool_call"]
    assert len(tool_parts) == 1 and tool_parts[0].status == "failed"
    assert tool_parts[0].summary == "Tool failed"
