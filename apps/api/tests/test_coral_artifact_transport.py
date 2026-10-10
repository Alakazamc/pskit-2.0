"""Provider-owned opaque files remain readable after restarting the CORAL MCP."""

import base64
import hashlib

import pytest

from integrations.coral.artifacts import CoralArtifacts


def test_restart_reads_opaque_chunks_and_supports_existing_content_ids(tmp_path):
    workspace = tmp_path / ("8" * 32)
    workspace.mkdir()
    raw = b"sequence\nACGU\n"
    path = workspace / "candidates.csv"
    path.write_bytes(raw)
    ref = CoralArtifacts(tmp_path).register(path, workspace)
    restarted = CoralArtifacts(tmp_path)
    chunk = restarted.read(ref["id"], 0, 8)
    assert base64.b64decode(chunk["data_base64"]) == b"sequence"
    assert chunk["eof"] is False
    last = restarted.read(ref["id"], 8, 100)
    assert base64.b64decode(last["data_base64"]) == b"\nACGU\n"
    assert last["eof"] is True
    old = "provider-" + hashlib.sha256(raw).hexdigest()[:24]
    assert restarted.read(old, 0, 100)["sha256"] == ref["sha256"]
    with pytest.raises(ValueError):
        restarted.read("../../etc/passwd", 0, 10)
    with pytest.raises(ValueError):
        restarted.read(ref["id"], -1, 10)
    with pytest.raises(ValueError):
        restarted.read(ref["id"], 0, 524289)
    path.unlink()
    path.symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        restarted.read(ref["id"], 0, 100)
def test_provider_installer_preserves_native_functions_and_is_idempotent():
    import ast

    from integrations.coral.install_artifacts import patched_source

    original = '''import asyncio
from pathlib import Path
def _artifact(path: Path, workspace: Path):
    return {"id": "old"}
def create_mcp_server(driver=None, workspace_root=None):
    root = Path(workspace_root)
    mcp = FakeMcp()
    async def native_model(value):
        return await driver.run(value)
    return mcp
'''
    updated = patched_source(original)
    assert 'async def native_model(value):' in updated
    assert 'return await driver.run(value)' in updated
    assert 'pskit.artifacts.read' in updated
    assert 'CoralArtifacts' in updated
    ast.parse(updated)
    assert patched_source(updated) == updated
