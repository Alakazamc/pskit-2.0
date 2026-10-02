"""Checks for the backend docstring inventory command."""

from scripts.check_docstrings import main


def test_checker_reports_missing_functions_and_fails_in_check_mode(tmp_path, capsys):
    module = tmp_path / "example.py"
    module.write_text(
        "def documented(value: int) -> int:\n"
        "    \"\"\"Return the input value.\"\"\"\n"
        "    return value\n\n"
        "def undocumented(value: int) -> int:\n"
        "    return value\n",
    )

    assert main(["--root", str(tmp_path)]) == 0
    assert "example.py: 1 missing" in capsys.readouterr().out
    assert main(["--root", str(tmp_path), "--check"]) == 1
