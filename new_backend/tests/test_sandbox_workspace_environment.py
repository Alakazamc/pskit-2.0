"""The coordinator retains existing attempt-scoped workspace capabilities."""

from app.services.sandbox_sessions import pi_environment


def test_sandbox_preserves_workspace_scope_without_host_secrets():
    scoped = {
        "PSKIT_USER_ID": "synthetic-owner", "PSKIT_RUN_ID": "run-1",
        "PSKIT_AGENT_TOOL_TOKEN": "scoped-token",
        "PSKIT_SESSION_ID": "session-1", "PSKIT_ATTEMPT_ID": "attempt-1",
        "PSKIT_WORKSPACE_TOOL_TOKEN": "scoped-workspace-token",
        "PSKIT_WORKSPACE_CAPABILITIES_JSON": '{"read":true,"write":true}',
        "MODEL_GATEWAY_API_KEY": "run-1.scoped-token",
    }
    result = pi_environment({**scoped, "SUPABASE_SECRET_KEY": "must-not-pass",
                             "DEEPSEEK_API_KEY": "must-not-pass", "HOME": "/host/private"})
    assert all(result.get(key) == value for key, value in scoped.items())
    assert not {"SUPABASE_SECRET_KEY", "DEEPSEEK_API_KEY", "HOME"}.intersection(result)
    assert result["PSKIT_INTERNAL_API_URL"] == "http://sandbox-gateway:8080"
