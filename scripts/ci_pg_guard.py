"""Fail CI if required PostgreSQL cases are skipped or never execute."""

import os

import pytest

_passed = 0
_skipped: list[str] = []


def pytest_configure(config):
    global _passed
    _passed = 0
    _skipped.clear()
    if os.environ.get("PSKIT_CI_REQUIRE_POSTGRES") == "1" and not os.environ.get(
        "TEST_POSTGRES_DSN"
    ):
        raise pytest.UsageError("TEST_POSTGRES_DSN is required for the CI PostgreSQL gate")


def pytest_runtest_logreport(report):
    global _passed
    postgres = "/postgres/" in report.nodeid.replace("\\", "/")
    if report.when == "call" and report.passed and postgres:
        _passed += 1
    if report.skipped and (postgres or "POSTGRES_DSN" in str(report.longrepr)):
        _skipped.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if _skipped or (os.environ.get("PSKIT_CI_EXPECT_POSTGRES") == "1" and not _passed):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if _skipped:
        terminalreporter.write_sep("=", "CI forbids skipped PostgreSQL cases", red=True)
        for nodeid in _skipped:
            terminalreporter.write_line(nodeid, red=True)
    if os.environ.get("PSKIT_CI_EXPECT_POSTGRES") == "1":
        terminalreporter.write_line(f"PostgreSQL gate: {_passed} passed cases; {len(_skipped)} skipped")
