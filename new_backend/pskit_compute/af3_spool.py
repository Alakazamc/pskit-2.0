"""MCP boundary over the existing durable AF3 model spool, independent of inference."""

import fcntl
import hashlib
import json
import uuid
from pathlib import Path

from app.contracts.capabilities import Af3FoldInput
from scripts.af3_receiver import atomic_json


class Af3Spool:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, task_id):
        if str(uuid.UUID(task_id)) != task_id:
            raise ValueError("AF3_INVALID_TASK_ID")
        directory = self.root / task_id
        if directory.is_symlink():
            raise ValueError("AF3_INVALID_TASK_DIRECTORY")
        return directory

    def submit(self, task_id, fold_input):
        Af3FoldInput.model_validate(fold_input)
        directory = self.directory(task_id)
        directory.mkdir(exist_ok=True)
        with (directory / ".submit.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = directory / "input.json"
            if path.exists():
                if json.loads(path.read_text()) != fold_input:
                    raise ValueError("AF3_INPUT_CONFLICT")
            else:
                atomic_json(path, fold_input)
        return self.status(task_id)

    def status(self, task_id):
        directory = self.directory(task_id)
        outcome_path = directory / "outcome.json"
        if not outcome_path.exists():
            return {"status": "pending", "job_id": task_id}
        outcome = json.loads(outcome_path.read_text())
        simulated = bool(outcome.get("simulation", False))
        usage = outcome.get("usage") or {
            "wall_ms": None, "cpu_core_ms": None,
            "gpu_device_ms": int(outcome["actual_gpu_minutes"]) * 60000,
            "gpu_count": 0 if simulated else 1, "source": "estimated",
        }
        artifacts = []
        if outcome["status"] == "completed":
            for path in sorted((directory / "output").rglob("*")):
                if not path.is_file() or path.is_symlink() or path.suffix not in {".cif", ".json"}:
                    continue
                if path.stat().st_size > 20 * 1024 * 1024:
                    raise ValueError("AF3_ARTIFACT_TOO_LARGE")
                relative = path.relative_to(directory / "output").as_posix()
                artifacts.append({
                    "id": hashlib.sha256((task_id + "/" + relative).encode()).hexdigest()[:32],
                    "name": relative.replace("/", "__"),
                    "kind": "structure" if path.suffix == ".cif" else "data",
                    "available": True, "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                })
        report = {"status": outcome["status"], "job_id": task_id,
                  "usage": usage, "artifacts": artifacts}
        if outcome["status"] == "completed":
            report["result"] = {"actual_gpu_minutes": outcome["actual_gpu_minutes"],
                                "simulation": simulated}
        else:
            report["error"] = {"code": "AF3_MODEL_FAILED", "message": "AF3 computation failed"}
        return report


def create_mcp(spool, *, host="127.0.0.1", port=18187):
    """Serve the persistent AF3 model using the same job-style MCP as remote models."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("PSKit AF3", host=host, port=port, stateless_http=True, json_response=True)

    @mcp.tool(name="af3.submit")
    async def submit(task_id: str, fold_input: dict) -> dict:
        return spool.submit(task_id, fold_input)

    @mcp.tool(name="af3.status")
    async def status(job_id: str) -> dict:
        return spool.status(job_id)

    return mcp
