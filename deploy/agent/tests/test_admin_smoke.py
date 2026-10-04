"""The management acceptance harness requires a disposable PostgreSQL target."""

import pytest

from deploy.agent.scripts.admin_smoke import acceptance_command


@pytest.mark.parametrize("dsn", ["", "postgresql://user:secret@production:5432/app"])
def test_admin_acceptance_refuses_implicit_or_nonlocal_database(dsn):
    with pytest.raises(ValueError, match="isolated local"):
        acceptance_command({"TEST_POSTGRES_DSN": dsn})


def test_admin_acceptance_runs_real_http_and_postgres_behavior_suites():
    command, directory = acceptance_command(
        {
            "TEST_POSTGRES_DSN": "postgresql://test:test@127.0.0.1:15433/test",
        }
    )
    assert directory.name == "new_backend"
    assert command[1:3] == ["-m", "pytest"]
    assert "tests/test_management_wiring.py" in command
    assert "tests/test_admin_identity.py" in command
    assert "tests/postgres/test_admin_audit.py" in command
    assert all(
        "test_admin" in path
        or "test_model_authorization" in path
        or "test_management_wiring" in path
        for path in command[3:-1]
    )
