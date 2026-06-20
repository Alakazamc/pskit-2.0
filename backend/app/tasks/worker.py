from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import asyncio
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.service import register_local_artifact, resolve_owned_local_artifact, task_artifact_dir
from app.config import get_settings
from app.db.models import AgentSession, Artifact, Task, User, now_utc
from app.db.session import SessionLocal, init_db


class TaskWorkerError(Exception):
    pass


def claim_next_task(db: Session) -> Task | None:
    task = db.scalar(
        select(Task)
        .where(Task.status == "queued")
        .order_by(Task.created_at.asc())
        .limit(1)
    )
    if not task:
        return None
    task.status = "running"
    task.progress = 0.05
    task.started_at = now_utc()
    task.updated_at = now_utc()
    db.commit()
    db.refresh(task)
    return task


def resolve_owned_input_file(db: Session, user: User, args: dict, path_key: str = "pdb_path") -> Path:
    artifact_id = args.get("artifact_id")
    if artifact_id:
        return resolve_owned_local_artifact(db, user, artifact_id)

    raw_path = args.get(path_key)
    if not raw_path:
        raise TaskWorkerError(f"Missing {path_key} or artifact_id")

    settings = get_settings()
    root = settings.artifact_dir.resolve()
    path = Path(str(raw_path)).resolve()
    if root not in path.parents and path != root:
        raise TaskWorkerError(f"{path_key} must point to a registered artifact under {root}")
    if not path.exists() or not path.is_file():
        raise TaskWorkerError(f"{path_key} does not exist: {path}")

    object_key = str(path.relative_to(root))
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.user_id == user.id,
            Artifact.storage_backend == "local",
            Artifact.object_key == object_key,
        )
    )
    if not artifact:
        raise TaskWorkerError(f"{path_key} is not owned by current user or is not registered")
    return path


def legacy_env() -> tuple[Path, dict[str, str]]:
    settings = get_settings()
    legacy_root = settings.pskit_legacy_root
    if not legacy_root:
        raise TaskWorkerError("PSKIT_LEGACY_ROOT is not configured")
    legacy_root = legacy_root.resolve()
    if not legacy_root.exists():
        raise TaskWorkerError(f"PSKIT_LEGACY_ROOT does not exist: {legacy_root}")

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{legacy_root}:{env.get('PYTHONPATH', '')}".rstrip(":")
    if settings.pskit_model_parameters:
        env["PSKIT_MODEL_PARAMETERS"] = str(settings.pskit_model_parameters)
    if settings.pskit_foldseek:
        env["PSKIT_FOLDSEEK"] = str(settings.pskit_foldseek)
    if settings.pskit_dssp:
        env["PSKIT_DSSP"] = str(settings.pskit_dssp)
    if settings.pskit_af3_db_dir:
        env["PSKIT_AF3_DB_DIR"] = str(settings.pskit_af3_db_dir)
    if settings.pskit_af3_model_dir:
        env["PSKIT_AF3_MODEL_DIR"] = str(settings.pskit_af3_model_dir)
    env["PSKIT_AF3_IMAGE"] = settings.pskit_af3_image
    env["PSKIT_AF3_GPU_DEVICE"] = settings.pskit_af3_gpu_device
    return legacy_root, env


def run_command(command: list[str], output_dir: Path) -> dict:
    settings = get_settings()
    legacy_root, env = legacy_env()
    completed = subprocess.run(
        command,
        cwd=legacy_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=settings.task_subprocess_timeout_seconds,
    )
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        raise TaskWorkerError(
            f"Command failed with exit code {completed.returncode}. "
            f"See {stdout_path.name} and {stderr_path.name}."
        )
    return {"stdout": completed.stdout or "", "stderr": completed.stderr or ""}


def require_result_file_stdout(run_result: dict, task_label: str) -> None:
    stdout = (run_result.get("stdout") or "").strip()
    if "result file:" not in stdout:
        raise TaskWorkerError(stdout or f"{task_label} produced no result file")


def raise_if_legacy_error(output_dir: Path) -> None:
    error_path = output_dir / "error.json"
    if not error_path.exists():
        return
    try:
        payload = json.loads(error_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TaskWorkerError(f"Legacy task wrote invalid error.json: {exc}") from exc
    if payload:
        preview = json.dumps(payload, ensure_ascii=False)[:2000]
        raise TaskWorkerError(f"Legacy task reported errors: {preview}")


def write_params(path: Path, params: dict) -> None:
    path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")


def run_binding_site_task(db: Session, user: User, task: Task, output_dir: Path) -> dict:
    args = task.input_json or {}
    pdb_path = resolve_owned_input_file(db, user, args)
    ligand_type = str(args.get("ligand_type", "RNA")).upper()
    if ligand_type not in {"DNA", "RNA"}:
        raise TaskWorkerError("ligand_type must be DNA or RNA")
    command = [
        sys.executable,
        "-m",
        "pskit.ai.INABe",
        "--pdb_path",
        str(pdb_path),
        "--output_dir",
        str(output_dir),
        "--ligand_type",
        ligand_type,
    ]
    result = run_command(command, output_dir)
    require_result_file_stdout(result, "Binding-site prediction")
    return result


def run_interaction_task(task: Task, output_dir: Path) -> dict:
    args = task.input_json or {}
    protein_seq = args.get("protein_seq") or args.get("protein_sequence")
    nucleic_seq = args.get("nucleic_acid_seq") or args.get("nucleic_sequence")
    if not protein_seq or not nucleic_seq:
        raise TaskWorkerError("protein_sequence and nucleic_sequence are required")
    command = [
        sys.executable,
        "-m",
        "pskit.ai.PAIR",
        "--protein_seq",
        str(protein_seq),
        "--nucleic_acid_seq",
        str(nucleic_seq),
        "--output_dir",
        str(output_dir),
    ]
    result = run_command(command, output_dir)
    require_result_file_stdout(result, "Interaction prediction")
    return result


def run_empirical_features_task(db: Session, user: User, task: Task, output_dir: Path) -> dict:
    args = task.input_json or {}
    pdb_path = resolve_owned_input_file(db, user, args)
    input_dir = output_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(pdb_path, input_dir / pdb_path.name)
    params = {
        "task_id": str(task.id),
        "task_name": "emp_feats",
        "input_method": "file",
        "ids": "",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "emp_feats": args.get("emp_feats", "dssp"),
        "rosetta_relax": str(args.get("rosetta_relax", "false")).lower(),
    }
    params_path = output_dir / "params.json"
    write_params(params_path, params)
    result = run_command([sys.executable, "-m", "pskit.ai.run_pskit", str(params_path)], output_dir)
    raise_if_legacy_error(output_dir)
    return result


def run_alphafold3_task(task: Task, output_dir: Path) -> dict:
    args = task.input_json or {}
    entities = args.get("entities") or []
    protein_sequences = args.get("protein_sequences") or []
    if not entities and not protein_sequences:
        raise TaskWorkerError("entities or protein_sequences are required")
    params = {
        "task_id": str(task.id),
        "task_name": "af3_predict",
        "input_method": "sequence",
        "ids": "",
        "input_dir": str(output_dir / "input"),
        "output_dir": str(output_dir),
        "job_name": args.get("job_name", f"pskit2_{str(task.id)[:8]}"),
        "entities": json.dumps(entities, ensure_ascii=False),
        "protein_sequences": json.dumps(protein_sequences, ensure_ascii=False),
        "model_seed": int(args.get("model_seed", 42)),
        "num_diffusion_samples": int(args.get("num_diffusion_samples", 5)),
        "gpu_device": str(args.get("gpu_device", get_settings().pskit_af3_gpu_device)),
        "max_template_date": str(args.get("max_template_date", "2021-09-30")),
    }
    (output_dir / "input").mkdir(parents=True, exist_ok=True)
    params_path = output_dir / "params.json"
    write_params(params_path, params)
    result = run_command([sys.executable, "-m", "pskit.ai.run_pskit", str(params_path)], output_dir)
    raise_if_legacy_error(output_dir)
    return result


async def call_remote_rna_expert(args: dict) -> dict:
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:
        raise TaskWorkerError(f"MCP Python SDK is not available: {exc}") from exc

    settings = get_settings()
    payload = {
        "pdb_id": str(args.get("pdb_id") or "").upper(),
        "chain": str(args.get("chain") or "A"),
        "num_samples": int(args.get("num_samples", 3)),
    }
    if args.get("length") is not None:
        payload["length"] = int(args["length"])
    if not payload["pdb_id"]:
        raise TaskWorkerError("pdb_id is required")

    async with sse_client(settings.remote_rna_expert_sse_url) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result = await session.call_tool("generate_rna_for_protein", payload)

    if getattr(result, "isError", False):
        raise TaskWorkerError(str(result))
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured

    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"status": "success", "text": text}
            if isinstance(parsed, dict):
                return parsed
    return {"status": "success", "raw": str(result)}


def run_remote_rna_expert_task(task: Task, output_dir: Path) -> dict:
    args = task.input_json or {}
    result = asyncio.run(call_remote_rna_expert(args))
    json_path = output_dir / "remote_rna_expert_result.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    generated = result.get("generated_rnas") if isinstance(result, dict) else None
    if isinstance(generated, list):
        csv_path = output_dir / "remote_rna_expert_sequences.csv"
        with csv_path.open("w", encoding="utf-8") as handle:
            handle.write("id,sequence,length\n")
            for item in generated:
                if not isinstance(item, dict):
                    continue
                handle.write(
                    f"{item.get('id', '')},{item.get('sequence', '')},{item.get('length', '')}\n"
                )
    return {"stdout": json.dumps(result, ensure_ascii=False), "stderr": ""}


def classify_artifact(path: Path) -> tuple[str, str | None]:
    suffix = path.suffix.lower()
    if suffix in {".pdb", ".cif", ".mmcif"}:
        return "structure", "chemical/x-cif" if suffix in {".cif", ".mmcif"} else "chemical/x-pdb"
    if suffix == ".csv":
        return "table", "text/csv"
    if suffix == ".json":
        return "json", "application/json"
    if suffix in {".log", ".txt"}:
        return "log", "text/plain"
    if suffix in {".npy", ".pkl", ".pt", ".pth"}:
        return "model_feature", None
    return "file", None


def register_task_outputs(db: Session, user: User, task: Task, output_dir: Path) -> list[dict]:
    artifacts = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        kind, mime_type = classify_artifact(path)
        artifact = register_local_artifact(
            db,
            user,
            task.session_id,
            task.id,
            path,
            kind=kind,
            mime_type=mime_type,
        )
        artifacts.append(
            {
                "artifact_id": str(artifact.id),
                "filename": artifact.filename,
                "kind": artifact.kind,
                "download_url": f"/api/files/{artifact.id}/download",
            }
        )
    return artifacts


def execute_task(db: Session, task: Task) -> Task:
    user = db.get(User, task.user_id)
    if not user:
        raise TaskWorkerError("Task owner no longer exists")
    if task.session_id:
        session = db.get(AgentSession, task.session_id)
        if not session or session.user_id != user.id:
            raise TaskWorkerError("Task session is missing or not owned by task user")

    output_dir = task_artifact_dir(user.id, task.id)
    task.progress = 0.15
    task.updated_at = now_utc()
    db.commit()

    try:
        if task.task_type == "predict_binding_sites":
            run_result = run_binding_site_task(db, user, task, output_dir)
        elif task.task_type == "predict_interaction":
            run_result = run_interaction_task(task, output_dir)
        elif task.task_type == "extract_empirical_features":
            run_result = run_empirical_features_task(db, user, task, output_dir)
        elif task.task_type == "run_alphafold3":
            run_result = run_alphafold3_task(task, output_dir)
        elif task.task_type == "remote_rna_expert__generate_rna_for_protein":
            run_result = run_remote_rna_expert_task(task, output_dir)
        else:
            raise TaskWorkerError(f"Unsupported worker task type: {task.task_type}")

        artifacts = register_task_outputs(db, user, task, output_dir)
        task.status = "succeeded"
        task.progress = 1.0
        task.output_json = {
            "output_dir": str(output_dir),
            "artifacts": artifacts,
            "stdout_preview": run_result.get("stdout", "")[:2000],
        }
        task.error_type = None
        task.error_message = None
    except Exception as exc:
        artifacts = register_task_outputs(db, user, task, output_dir)
        task.status = "failed"
        task.progress = 1.0
        task.error_type = exc.__class__.__name__
        task.error_message = str(exc)
        task.output_json = {
            "output_dir": str(output_dir),
            "artifacts": artifacts,
        }
    task.finished_at = now_utc()
    task.updated_at = now_utc()
    db.commit()
    db.refresh(task)
    return task


def run_once() -> bool:
    init_db()
    db = SessionLocal()
    try:
        task = claim_next_task(db)
        if not task:
            return False
        execute_task(db, task)
        print(f"{task.id} {task.task_type} {task.status}")
        return True
    finally:
        db.close()


def run_forever(poll_seconds: float = 2.0) -> None:
    while True:
        did_work = run_once()
        if not did_work:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "forever":
        run_forever()
    else:
        run_once()
