"""The application can select the remote Pi sandbox without a local Pi binary."""

from app.adapters.live.sandbox_pi import SandboxPiRunner
from app.config import Settings
from app.main import create_app


def test_sandbox_mode_uses_private_runner_instead_of_local_pi(tmp_path):
    app = create_app(
        Settings(
            agent_runtime="pi",
            agent_db_path=str(tmp_path / "agent.sqlite3"),
            pi_executable="/missing/pi",
            pi_execution="sandbox",
            sandbox_manager_url="http://sandbox-manager:8090",
            sandbox_manager_token="manager-token-123456",
            model_gateway_model="claude-opus-4-8",
        )
    )

    assert isinstance(app.state.agent_service.runner, SandboxPiRunner)
