from pathlib import Path

from integrations.coral.launch_artifact_server import build_environment


def test_restart_preserves_native_module_paths_and_uses_absolute_workspaces(tmp_path):
    root = tmp_path / 'provider'
    root.mkdir()
    (root / '.pskit-mcp.runtime.env').write_text(
        'PYTHONPATH=/provider/deps:/provider/src\nPRIVATE_MODEL_KEY=preserved\n'
    )
    foldseek = tmp_path / 'tools' / 'foldseek'
    python = Path('/runtime/bin/python')
    env = build_environment(root, python, {'PATH': '/usr/bin'}, foldseek=foldseek)
    assert env['PYTHONPATH'].split(':') == ['/provider/deps', '/provider/src', str(root)]
    assert env['PRIVATE_MODEL_KEY'] == 'preserved'
    assert env['PATH'].split(':')[:2] == ['/runtime/bin', str(foldseek.parent)]
    assert env['FOLDSEEK_BINARY'] == str(foldseek)
    assert Path(env['PSKIT_MCP_WORKSPACE_ROOT']).is_absolute()
    assert Path(env['PSKIT_MCP_WORKSPACE_ROOT']) == root / 'pskit_mcp_runs'


def test_existing_workspace_and_provider_environment_remain_effective(tmp_path):
    (tmp_path / '.pskit-mcp.runtime.env').write_text(
        "PSKIT_MCP_WORKSPACE_ROOT='./existing'\nPYTHONPATH=/source/src\n"
    )
    env = build_environment(tmp_path, Path('/env/bin/python'), {'PATH': '/usr/bin'})
    assert env['PSKIT_MCP_WORKSPACE_ROOT'] == str(tmp_path / 'existing')
    assert env['PYTHONPATH'] == '/source/src:' + str(tmp_path)


def test_invalid_python_is_rejected_before_reading_or_stopping_provider(tmp_path):
    import pytest

    from integrations.coral.launch_artifact_server import main

    # No PID or environment files: executable validation must precede either read.
    with pytest.raises(ValueError, match='INVALID_PROVIDER_EXECUTABLE'):
        main(['--provider-root', str(tmp_path), '--python', str(tmp_path / 'missing-python')])
