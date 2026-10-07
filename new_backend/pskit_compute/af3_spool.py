"""MCP boundary over the existing durable AF3 model spool, independent of inference."""

import base64
import fcntl
import hashlib
import json
import re
import uuid
from pathlib import Path

from pydantic import ValidationError

from app.contracts.capabilities import Af3FoldInput
from scripts.af3_receiver import atomic_json


class Af3Spool:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def directory(self, task_id):
        # The unchanged native AF3 engine consumes UUID directories only. Keep
        # public compute IDs intact while mapping their spool path deterministically.
        if re.fullmatch(r"compute-[0-9a-f]{32}", task_id):
            directory_id = str(uuid.uuid5(uuid.NAMESPACE_OID, "pskit:" + task_id))
        elif str(uuid.UUID(task_id)) == task_id:
            directory_id = task_id
        else:
            raise ValueError("AF3_INVALID_TASK_ID")
        directory = self.root / directory_id
        if directory.is_symlink():
            raise ValueError("AF3_INVALID_TASK_DIRECTORY")
        return directory

    def submit(self, task_id, fold_input):
        try:
            Af3FoldInput.model_validate(fold_input)
        except ValidationError:
            # A deterministic rejected input must release the lane without ever
            # publishing input.json or charging inference time.
            return {"status": "failed", "job_id": task_id, "artifacts": [],
                    "error": {"code": "INVALID_AF3_INPUT", "message": "Invalid AlphaFold 3 input"},
                    "usage": {"source": "estimated", "gpu_device_ms": 0, "gpu_count": 0}}
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
            output = directory / "output"
            if output.is_symlink():
                raise ValueError("AF3_ARTIFACT_PATH_INVALID")
            for path in sorted(output.rglob("*")):
                if not path.is_file() or path.is_symlink() or path.suffix not in {".cif", ".json"}:
                    continue
                if not path.resolve().is_relative_to(output.resolve()):
                    raise ValueError("AF3_ARTIFACT_PATH_INVALID")
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

    def read_artifact(self, job_id, artifact_id, offset=0, length=512 * 1024):
        if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0
                or not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= 512 * 1024):
            raise ValueError("ARTIFACT_REQUEST_INVALID")
        report = self.status(job_id)
        artifact = next((item for item in report.get("artifacts", [])
                         if item["id"] == artifact_id), None)
        if artifact is None:
            raise ValueError("ARTIFACT_NOT_FOUND")
        output = self.directory(job_id) / "output"
        # Resolve only the ID already enumerated from this job; never accept an input path.
        path = next(path for path in output.rglob("*") if path.is_file()
                    and hashlib.sha256((job_id + "/" + path.relative_to(output).as_posix())
                                       .encode()).hexdigest()[:32] == artifact_id)
        if offset > artifact["size"]:
            raise ValueError("ARTIFACT_REQUEST_INVALID")
        with path.open("rb") as file:
            file.seek(offset)
            raw = file.read(length)
        return {"artifact_id": artifact_id, "offset": offset, "size": artifact["size"],
                "sha256": artifact["sha256"], "data_base64": base64.b64encode(raw).decode(),
                "eof": offset + len(raw) == artifact["size"]}


def create_mcp(spool, *, host="127.0.0.1", port=18187):
    """Serve the persistent AF3 model using the same job-style MCP as remote models."""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = FastMCP("PSKit AF3", host=host, port=port, stateless_http=True, json_response=True,
                  transport_security=TransportSecuritySettings(
                      enable_dns_rebinding_protection=True,
                      allowed_hosts=[f"{host}:*", f"{host}", "localhost:*"],
                      allowed_origins=[],
                  ))

    @mcp.tool(name="af3.submit")
    async def submit(task_id: str, fold_input: dict) -> dict:
        return spool.submit(task_id, fold_input)

    @mcp.tool(name="af3.status")
    async def status(job_id: str) -> dict:
        return spool.status(job_id)

    @mcp.tool(name="pskit.artifacts.read")
    async def read_artifact(job_id: str, artifact_id: str, offset: int = 0,
                            length: int = 512 * 1024) -> dict:
        return spool.read_artifact(job_id, artifact_id, offset, length)

    return mcp
