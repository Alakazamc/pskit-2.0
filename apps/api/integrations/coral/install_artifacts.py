"""Idempotent source patch for the PSKit-owned CORAL MCP (no inference changes)."""

import argparse
import ast
import shutil
from pathlib import Path

MARKER = "# PSKit owned artifact transport v1"


def patched_source(source):
    if MARKER in source:
        return source
    tree = ast.parse(source)
    artifact = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "_artifact")
    factory = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name == "create_mcp_server")
    assignment = next(node for node in factory.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "mcp"
                              for target in node.targets))
    lines = source.splitlines(keepends=True)
    # Patch from the bottom, leaving all four native model implementations intact.
    lines[assignment.end_lineno:assignment.end_lineno] = [
        "\n    " + MARKER + "\n",
        "    @mcp.tool(name='pskit.artifacts.read')\n",
        "    async def read_artifact(artifact_id: str, job_id: str, offset: int = 0,\n",
        "                            length: int = 512 * 1024) -> dict:\n",
        "        from .artifacts import CoralArtifacts\n",
        "        return await asyncio.to_thread(CoralArtifacts(root).read, artifact_id, offset, length)\n",
    ]
    lines[artifact.lineno - 1:artifact.end_lineno] = [
        "def _artifact(path: Path, workspace: Path):\n",
        "    from .artifacts import CoralArtifacts\n",
        "    return CoralArtifacts(workspace.resolve().parent).register(path, workspace)\n",
    ]
    patched = "".join(lines)
    ast.parse(patched)
    return patched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-root", type=Path, required=True)
    args = parser.parse_args()
    server = args.provider_root / "pskit_mcp/server.py"
    original = server.read_text()
    patched = patched_source(original)
    backup = server.with_suffix(".py.pre-artifact-transport")
    if patched != original:
        if backup.exists() and backup.read_text() != original:
            raise RuntimeError("REVIEW_EXISTING_PROVIDER_BACKUP")
        if not backup.exists():
            shutil.copy2(server, backup)
    shutil.copy2(Path(__file__).with_name("artifacts.py"), server.with_name("artifacts.py"))
    if patched != original:
        server.write_text(patched)
    print("CORAL artifact reader installed; inference implementations retained")


if __name__ == "__main__":
    main()
