from __future__ import annotations

import asyncio
import csv
import contextlib
import hashlib
import json
import logging
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.artifacts.service import register_local_artifact, resolve_owned_local_artifact, task_artifact_dir
from app.config import get_settings
from app.db.models import (
    AgentSession,
    Artifact,
    Candidate,
    CandidateTrack,
    ResearchRun,
    ResearchTaskLink,
    ServiceHeartbeat,
    Task,
    TaskExecutionLease,
    User,
    ensure_utc,
    now_utc,
)
from app.db.session import SessionLocal, init_db
from app.harness.runtime_wiring import validate_persisted_dispatch
from app.research.service import (
    ensure_research_target_hash,
    link_task_to_research_run,
    public_task_error_message,
    refresh_research_run_progress,
)
from app.tasks.service import create_retry_task
from app.tools.mcp_adapters import (
    MCPAdapterError,
    build_pepccd_mcp_payload,
    extract_mcp_result,
    safe_mcp_exception_message,
    select_pepccd_mcp_tool_name,
)
from app.tools.science_adapters import (
    ScientificToolDependencyError,
    build_af3_output_normalization_command,
    build_foldseek_command,
    build_sequence_search_command,
    cleanup_scientific_scratch_dir,
    normalize_candidate_result,
    parse_homology_hits,
)
from app.tools.structure import ChainSelect, parse_structure, save_selected

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised on Windows CI/development
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - exercised on Linux production
    _msvcrt = None


class TaskWorkerError(Exception):
    pass


class Af3GpuMemoryUnavailable(TaskWorkerError):
    """AlphaFold3 因 GPU 显存不足或资源占用而无法完成。"""


class Af3ExecutionTimeout(TaskWorkerError):
    """AlphaFold3 超过受控执行时限。"""


class TaskLeaseLost(TaskWorkerError):
    pass


def _af3_gpu_lock_path(data_dir: Path, gpu_device: str) -> Path:
    safe_device = "".join(
        character if character.isalnum() else "_"
        for character in str(gpu_device)
    ) or "default"
    return Path(data_dir) / ".pskit-locks" / f"af3-gpu-{safe_device}.lock"


@contextlib.contextmanager
def af3_gpu_lock(data_dir: Path, gpu_device: str):
    """跨 Worker 进程串行化同一 GPU 上的 AF3 任务。"""

    lock_path = _af3_gpu_lock_path(data_dir, gpu_device)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if _fcntl is not None:
            _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_EX)
        elif _msvcrt is not None:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"0")
                lock_file.flush()
            lock_file.seek(0)
            _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_LOCK, 1)
        else:  # pragma: no cover - Python platforms supported by PSKit have one
            raise TaskWorkerError("No file-lock implementation is available")
        try:
            yield lock_path
        finally:
            if _fcntl is not None:
                _fcntl.flock(lock_file.fileno(), _fcntl.LOCK_UN)
            elif _msvcrt is not None:
                lock_file.seek(0)
                _msvcrt.locking(lock_file.fileno(), _msvcrt.LK_UNLCK, 1)


def _inspect_af3_prediction_container(container_id: str) -> bool | None:
    """判断镜像容器是否为 AF3 预测本体，而非同镜像科学子任务。"""

    try:
        completed = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.Path}}\t{{range .Args}}{{.}} {{end}}",
                container_id,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "无法读取 AF3 容器命令 container=%s error_type=%s",
            container_id,
            exc.__class__.__name__,
        )
        return None
    if completed.returncode != 0:
        logger.warning(
            "无法读取 AF3 容器命令 container=%s returncode=%s",
            container_id,
            completed.returncode,
        )
        return None
    command_text = (completed.stdout or "").lower()
    # JackHMMER/Foldseek/MMseqs2 复用 AF3 镜像但不占用 AF3 GPU 锁。
    scientific_subtask_markers = (
        "jackhmmer",
        "foldseek",
        "mmseqs",
        "hmmsearch",
        "hmmscan",
    )
    return not any(marker in command_text for marker in scientific_subtask_markers)


def snapshot_active_af3_containers(image: str) -> set[str] | None:
    """读取真正 AF3 预测容器快照；None 表示状态无法可靠读取。"""

    if shutil.which("docker") is None:
        return None
    try:
        completed = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"ancestor={image}",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("无法读取 AF3 容器快照 error_type=%s", exc.__class__.__name__)
        return None
    if completed.returncode != 0:
        logger.warning(
            "无法读取 AF3 容器快照 returncode=%s",
            completed.returncode,
        )
        return None
    active_ids = {
        line.strip()
        for line in (completed.stdout or "").splitlines()
        if line.strip()
    }
    af3_ids: set[str] = set()
    for container_id in sorted(active_ids):
        is_prediction = _inspect_af3_prediction_container(container_id)
        if is_prediction is None:
            return None
        if is_prediction:
            af3_ids.add(container_id)
    return af3_ids


def af3_gpu_free_memory_mib(gpu_device: str) -> int | None:
    """Read free GPU memory for the configured AF3 device."""

    if shutil.which("nvidia-smi") is None:
        return None
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "无法读取 AF3 GPU 剩余显存 device=%s error_type=%s",
            gpu_device,
            exc.__class__.__name__,
        )
        return None
    if completed.returncode != 0:
        logger.warning(
            "无法读取 AF3 GPU 剩余显存 device=%s returncode=%s",
            gpu_device,
            completed.returncode,
        )
        return None
    rows: dict[str, int] = {}
    for line in (completed.stdout or "").splitlines():
        fields = [field.strip() for field in line.split(",", maxsplit=1)]
        if len(fields) != 2:
            continue
        try:
            rows[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return rows.get(str(gpu_device))


def require_af3_gpu_memory_available(gpu_device: str) -> int:
    """Reject AF3 before container launch when the selected GPU is occupied."""

    try:
        minimum_free_mib = int(
            os.environ.get("PSKIT_AF3_MIN_FREE_MEMORY_MIB", "40000")
        )
    except ValueError as exc:
        raise Af3GpuMemoryUnavailable(
            "PSKIT_AF3_MIN_FREE_MEMORY_MIB must be an integer"
        ) from exc
    if minimum_free_mib < 1024:
        raise Af3GpuMemoryUnavailable(
            "PSKIT_AF3_MIN_FREE_MEMORY_MIB must be at least 1024"
        )
    free_mib = af3_gpu_free_memory_mib(gpu_device)
    if free_mib is None:
        raise Af3GpuMemoryUnavailable(
            "AlphaFold3 GPU memory availability cannot be read reliably"
        )
    if free_mib < minimum_free_mib:
        raise Af3GpuMemoryUnavailable(
            "AlphaFold3 GPU memory is unavailable or currently occupied "
            f"(free={free_mib} MiB, required={minimum_free_mib} MiB)"
        )
    return free_mib


def cleanup_new_af3_containers(
    image: str,
    baseline: set[str] | None,
    output_dir: Path,
) -> None:
    """超时后只清理本次 AF3 启动的新容器，避免误杀其他任务。"""

    if baseline is None:
        return
    current = snapshot_active_af3_containers(image)
    if current is None:
        return
    new_containers = sorted(current - baseline)
    if not new_containers:
        return
    cleanup_log = output_dir / "af3_container_cleanup.log"
    lines = [f"image={image}", f"containers={','.join(new_containers)}"]
    for container_id in new_containers:
        for action in ("stop", "rm"):
            command = ["docker", action]
            if action == "stop":
                command.extend(["--time", "5"])
            else:
                command.append("-f")
            command.append(container_id)
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                lines.append(
                    f"{action} {container_id} rc={completed.returncode} "
                    f"stdout={(completed.stdout or '').strip()[:500]} "
                    f"stderr={(completed.stderr or '').strip()[:500]}"
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                lines.append(
                    f"{action} {container_id} error={exc.__class__.__name__}"
                )
    cleanup_log.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _decode_process_output(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """终止 legacy Python、Docker CLI 及其同进程组子进程。"""

    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.communicate(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


logger = logging.getLogger(__name__)
_science_probe_lock = threading.Lock()
_science_probe_cache: tuple[float, datetime, dict] | None = None
_science_probe_inflight = False


class TaskLeaseHeartbeat:
    """用 Task.updated_at 续租，Worker 异常退出后可由下一实例安全回收。"""

    def __init__(self, task_id: UUID, lease_token: str) -> None:
        self.task_id = task_id
        self.lease_token = lease_token
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "TaskLeaseHeartbeat":
        interval = get_settings().task_heartbeat_seconds

        def heartbeat() -> None:
            while not self.stop_event.wait(interval):
                heartbeat_db = SessionLocal()
                try:
                    refresh_task_and_worker_lease(
                        heartbeat_db,
                        self.task_id,
                        self.lease_token,
                    )
                except TaskLeaseLost:
                    heartbeat_db.rollback()
                    logger.warning("任务租约已失效 task_id=%s", self.task_id)
                    self.stop_event.set()
                except Exception:
                    heartbeat_db.rollback()
                    logger.exception("任务租约心跳更新失败 task_id=%s", self.task_id)
                finally:
                    heartbeat_db.close()

        self.thread = threading.Thread(
            target=heartbeat,
            name=f"pskit-task-heartbeat-{str(self.task_id)[:8]}",
            daemon=True,
        )
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)


def refresh_task_and_worker_lease(
    db: Session,
    task_id: UUID,
    lease_token: str,
) -> None:
    """同时续租任务和 Worker 服务，避免长任务期间 readiness 误报失联。"""
    now = now_utc()
    refreshed_lease = db.execute(
        update(TaskExecutionLease)
        .where(
            TaskExecutionLease.task_id == task_id,
            TaskExecutionLease.lease_token == lease_token,
        )
        .values(
            expires_at=now
            + timedelta(seconds=get_settings().task_stale_after_seconds),
            updated_at=now,
        )
    )
    refreshed_task = db.execute(
        update(Task)
        .where(
            Task.id == task_id,
            Task.status == "running",
        )
        .values(updated_at=now)
    )
    if refreshed_lease.rowcount != 1 or refreshed_task.rowcount != 1:
        db.rollback()
        raise TaskLeaseLost("任务租约已被其他 Worker 回收")
    heartbeat = db.get(ServiceHeartbeat, "task-worker")
    if heartbeat is None:
        heartbeat = ServiceHeartbeat(
            name="task-worker",
            metadata_json={
                "pid": os.getpid(),
                "mode": "task",
                "science_ready": False,
                "missing_dependencies": ["full worker preflight not recorded"],
            },
        )
        db.add(heartbeat)
    heartbeat.updated_at = now
    heartbeat.metadata_json = {
        **(heartbeat.metadata_json or {}),
        "pid": os.getpid(),
        "mode": "task",
        **cached_science_dependency_summary(),
    }
    db.commit()
    # wzf：长任务心跳继续以非阻塞方式刷新深探针，不能把任务开始前的
    # 科学依赖快照冻结数十分钟。
    schedule_science_dependency_probe()


def recover_stale_tasks(db: Session) -> int:
    """终止失联尝试，并用新 Task 身份创建可审计的后继尝试。"""

    settings = get_settings()
    now = now_utc()
    stale_before = now_utc() - timedelta(seconds=settings.task_stale_after_seconds)
    candidates = db.scalars(
        select(Task)
        .where(
            Task.status == "running",
            Task.updated_at < stale_before,
        )
        .order_by(Task.created_at.asc())
        .limit(20)
    ).all()
    recovered = 0
    for candidate in candidates:
        lease = db.get(TaskExecutionLease, candidate.id)
        if lease is not None:
            if ensure_utc(lease.expires_at) >= now:
                continue
            removed = db.execute(
                delete(TaskExecutionLease).where(
                    TaskExecutionLease.task_id == candidate.id,
                    TaskExecutionLease.lease_token == lease.lease_token,
                    TaskExecutionLease.expires_at == lease.expires_at,
                )
            )
            if removed.rowcount != 1:
                db.rollback()
                continue
            status_conditions = [
                Task.id == candidate.id,
                Task.status == "running",
            ]
        else:
            status_conditions = [
                Task.id == candidate.id,
                Task.status == "running",
                Task.updated_at == candidate.updated_at,
            ]
        output = dict(candidate.output_json or {})
        attempt = int(output.get("_worker_attempt", 0))
        exhausted = attempt >= settings.task_max_attempts
        expired = db.execute(
            update(Task)
            .where(*status_conditions)
            .values(
                status="failed",
                progress=1.0,
                error_type=(
                    "TaskAttemptsExhausted" if exhausted else "TaskLeaseExpired"
                ),
                error_message=(
                    "Worker lease expired and retry limit was reached"
                    if exhausted
                    else "Worker lease expired; a new isolated retry was created"
                ),
                output_json={
                    **output,
                    "_lease_expired": True,
                },
                finished_at=now,
                updated_at=now,
            )
        )
        if expired.rowcount != 1:
            db.rollback()
            continue
        # wzf：失败旧尝试、创建后继尝试和科研状态刷新必须原子提交；
        # 中途进程退出时回滚为原 running 事实，下一轮仍能安全回收。
        db.expire_all()
        parent = db.get(Task, candidate.id)
        user = db.get(User, candidate.user_id)
        if parent is None or user is None:
            db.rollback()
            continue
        try:
            if not exhausted:
                create_retry_task(
                    db,
                    user,
                    parent,
                    client_retry_id=uuid5(
                        NAMESPACE_URL,
                        f"pskit-auto-retry:{parent.id}",
                    ),
                    reason="lease_expired",
                )
            else:
                sync_research_task_state(db, parent, [])
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "过期任务原子回收失败 task_id=%s",
                candidate.id,
            )
            continue
        recovered += 1
    return recovered


def claim_next_task(db: Session) -> Task | None:
    settings = get_settings()
    recover_stale_tasks(db)
    candidates = db.scalars(
        select(Task)
        .where(Task.status == "queued")
        .order_by(Task.created_at.asc())
        .limit(20)
    ).all()
    for candidate in candidates:
        output = dict(candidate.output_json or {})
        attempt = int(output.get("_worker_attempt", 0))
        if attempt >= settings.task_max_attempts:
            failed = db.execute(
                update(Task)
                .where(Task.id == candidate.id, Task.status == "queued")
                .values(
                    status="failed",
                    progress=1.0,
                    error_type="TaskAttemptsExhausted",
                    error_message="Worker retry limit was reached",
                    finished_at=now_utc(),
                    updated_at=now_utc(),
                )
            )
            if failed.rowcount:
                db.commit()
            continue
        now = now_utc()
        lease_token = str(uuid4())
        lease_owner = f"{os.getpid()}:{threading.current_thread().name}"
        claimed = db.execute(
            update(Task)
            .where(Task.id == candidate.id, Task.status == "queued")
            .values(
                status="running",
                progress=0.05,
                started_at=now,
                updated_at=now,
                finished_at=None,
                error_type=None,
                error_message=None,
                output_json={
                    **output,
                    "_worker_attempt": attempt + 1,
                    "_recovered_from_stale": bool(output.get("_retry_of_task_id")),
                },
            )
        )
        if claimed.rowcount == 1:
            db.add(
                TaskExecutionLease(
                    task_id=candidate.id,
                    lease_token=lease_token,
                    lease_owner=lease_owner,
                    attempt_no=attempt + 1,
                    expires_at=now
                    + timedelta(seconds=settings.task_stale_after_seconds),
                    updated_at=now,
                )
            )
            db.commit()
            return db.get(Task, candidate.id)
        db.rollback()
    return None


def fence_task_lease(db: Session, task_id: UUID, lease_token: str) -> TaskExecutionLease:
    """锁住当前租约，使任务输出与科研状态在同一受保护事务内提交。"""

    lease = db.scalar(
        select(TaskExecutionLease).where(
            TaskExecutionLease.task_id == task_id,
            TaskExecutionLease.lease_token == lease_token,
        )
    )
    task = db.get(Task, task_id)
    if (
        lease is None
        or task is None
        or task.status != "running"
        or ensure_utc(lease.expires_at) < now_utc()
    ):
        raise TaskLeaseLost("任务执行租约已失效")
    fenced = db.execute(
        update(TaskExecutionLease)
        .where(
            TaskExecutionLease.task_id == task_id,
            TaskExecutionLease.lease_token == lease_token,
        )
        .values(
            updated_at=now_utc(),
            expires_at=now_utc()
            + timedelta(seconds=get_settings().task_stale_after_seconds),
        )
    )
    if fenced.rowcount != 1:
        raise TaskLeaseLost("任务执行租约已被其他 Worker 回收")
    return lease


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


def legacy_python_executable() -> str:
    """返回旧版科研脚本专用解释器，避免其依赖污染 Worker 主环境。"""

    configured = getattr(get_settings(), "pskit_legacy_python", None)
    if configured is None:
        return sys.executable
    # wzf：虚拟环境的 python 通常是指向系统解释器的符号链接。这里只解析
    # 目标做存在性校验，实际执行必须保留虚拟环境入口，否则 Python 无法发现
    # 该虚拟环境中安装的 torch 等旧版科研依赖。
    path = Path(os.path.abspath(configured.expanduser()))
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(path, os.X_OK):
        raise TaskWorkerError(f"PSKIT_LEGACY_PYTHON is not executable: {path}")
    return str(path)


def run_external_command(
    command: list[str],
    output_dir: Path,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
    cleanup_process_group: bool = False,
    af3_container_image: str | None = None,
    af3_container_ids_before: set[str] | None = None,
) -> dict:
    settings = get_settings()
    timeout = int(
        timeout_seconds
        if timeout_seconds is not None
        else settings.task_subprocess_timeout_seconds
    )
    stdout_path = output_dir / "stdout.log"
    stderr_path = output_dir / "stderr.log"
    if cleanup_process_group:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name == "posix",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            partial_stdout = _decode_process_output(exc.stdout)
            partial_stderr = _decode_process_output(exc.stderr)
            _terminate_process_group(process)
            try:
                tail_stdout, tail_stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                tail_stdout, tail_stderr = "", ""
            stdout = partial_stdout + _decode_process_output(tail_stdout)
            stderr = partial_stderr + _decode_process_output(tail_stderr)
            stdout_path.write_text(stdout, encoding="utf-8")
            stderr_path.write_text(stderr, encoding="utf-8")
            if af3_container_image:
                cleanup_new_af3_containers(
                    af3_container_image,
                    af3_container_ids_before,
                    output_dir,
                )
            raise TaskWorkerError(
                f"Command timed out after {timeout} seconds. "
                f"Process group and AF3 child container cleanup was attempted; "
                f"see {stdout_path.name} and {stderr_path.name}."
            ) from exc
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
        if process.returncode != 0:
            raise TaskWorkerError(
                f"Command failed with exit code {process.returncode}. "
                f"See {stdout_path.name} and {stderr_path.name}."
            )
        return {"stdout": stdout or "", "stderr": stderr or ""}
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        stdout_path.write_text(stdout or "", encoding="utf-8")
        stderr_path.write_text(stderr or "", encoding="utf-8")
        raise TaskWorkerError(
            f"Command timed out after {timeout} seconds. "
            f"See {stdout_path.name} and {stderr_path.name}."
        ) from exc
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode != 0:
        raise TaskWorkerError(
            f"Command failed with exit code {completed.returncode}. "
            f"See {stdout_path.name} and {stderr_path.name}."
        )
    return {"stdout": completed.stdout or "", "stderr": completed.stderr or ""}


def run_command(
    command: list[str],
    output_dir: Path,
    *,
    timeout_seconds: int | None = None,
    cleanup_process_group: bool = False,
    af3_container_image: str | None = None,
    af3_container_ids_before: set[str] | None = None,
) -> dict:
    legacy_root, env = legacy_env()
    return run_external_command(
        command,
        output_dir,
        cwd=legacy_root,
        env=env,
        timeout_seconds=timeout_seconds,
        cleanup_process_group=cleanup_process_group,
        af3_container_image=af3_container_image,
        af3_container_ids_before=af3_container_ids_before,
    )


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


def classify_af3_resource_failure(
    output_dir: Path,
    error: TaskWorkerError,
) -> TaskWorkerError:
    """把 AF3 资源错误归一化为可安全公开的稳定类别。"""

    stderr_path = output_dir / "af3_stderr.log"
    stderr = ""
    if stderr_path.is_file():
        try:
            with stderr_path.open("rb") as handle:
                handle.seek(max(0, stderr_path.stat().st_size - 1024 * 1024))
                stderr = handle.read().decode("utf-8", errors="replace")
        except OSError:
            stderr = ""
    normalized = stderr.lower()
    if any(
        marker in normalized
        for marker in (
            "resource_exhausted",
            "out of memory",
            "cuda_error_out_of_memory",
            "failed to allocate memory",
        )
    ):
        return Af3GpuMemoryUnavailable(
            "AlphaFold3 GPU memory is unavailable or currently occupied"
        )
    if "timed out after" in str(error).lower():
        return Af3ExecutionTimeout(
            "AlphaFold3 exceeded the controlled execution timeout"
        )
    return error


def write_params(path: Path, params: dict) -> None:
    path.write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")


def run_binding_site_task(db: Session, user: User, task: Task, output_dir: Path) -> dict:
    args = task.input_json or {}
    pdb_path = resolve_owned_input_file(db, user, args)
    ligand_type = str(args.get("ligand_type", "RNA")).upper()
    if ligand_type not in {"DNA", "RNA"}:
        raise TaskWorkerError("ligand_type must be DNA or RNA")
    command = [
        legacy_python_executable(),
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
        legacy_python_executable(),
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
    result = run_command(
        [legacy_python_executable(), "-m", "pskit.ai.run_pskit", str(params_path)],
        output_dir,
    )
    raise_if_legacy_error(output_dir)
    return result


def resolve_configured_binary(path: Path | None, label: str) -> Path:
    if path is None:
        raise TaskWorkerError(f"{label} is not configured")
    if path.exists() and path.is_file() and os.access(path, os.X_OK):
        return path.resolve()
    resolved = shutil.which(str(path))
    if resolved:
        return Path(resolved)
    raise TaskWorkerError(f"{label} executable does not exist: {path}")


def require_database_path(path: Path | None, label: str) -> Path:
    if path is None:
        raise TaskWorkerError(f"{label} is not configured")
    if path.exists():
        return path.resolve()
    if path.parent.exists() and any(path.parent.glob(f"{path.name}*")):
        return path.resolve()
    raise TaskWorkerError(f"{label} does not exist: {path}")


def write_homology_summary(output_dir: Path, hits: list[dict], metadata: dict) -> Path:
    path = output_dir / "homology_hits.json"
    path.write_text(
        json.dumps({"hits": hits, "metadata": metadata}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def run_sequence_homology_task(task: Task, output_dir: Path) -> dict:
    args = task.input_json or {}
    sequence = "".join(str(args.get("protein_sequence") or "").split()).upper()
    if not sequence:
        raise TaskWorkerError("protein_sequence is required")
    invalid = sorted(set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"))
    if invalid:
        raise TaskWorkerError(
            f"protein_sequence contains unsupported symbols: {''.join(invalid)}"
        )
    if len(sequence) > 10000:
        raise TaskWorkerError("protein_sequence exceeds the 10000-residue limit")
    settings = get_settings()
    binary = resolve_configured_binary(
        settings.pskit_sequence_search_binary,
        "PSKIT_SEQUENCE_SEARCH_BINARY",
    )
    database = require_database_path(settings.pskit_sequence_search_db, "PSKIT_SEQUENCE_SEARCH_DB")
    query_path = output_dir / "query.fasta"
    result_path = output_dir / "sequence_homology_hits.tsv"
    temp_dir = output_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    query_path.write_text(f">query\n{sequence}\n", encoding="utf-8")
    max_hits = int(args.get("max_hits", 50))
    command = build_sequence_search_command(
        backend=settings.pskit_sequence_search_backend,
        binary=binary,
        query_path=query_path,
        database_path=database,
        output_path=result_path,
        temp_dir=temp_dir,
        max_hits=max_hits,
        evalue=float(args.get("evalue", 1e-3)),
        sensitivity=float(args.get("sensitivity", 7.5)),
        container_image=settings.pskit_af3_image,
    )
    try:
        run_result = run_external_command(command, output_dir)
        hits = parse_homology_hits(
            result_path,
            backend=settings.pskit_sequence_search_backend,
            max_hits=max_hits,
        )
    finally:
        # wzf：科学工具的 tmp 只承载索引与缓存，可能含内部符号链接；
        # 正式结果位于任务根目录，登记产物前必须清理临时工作区。
        cleanup_scientific_scratch_dir(output_dir, temp_dir)
    metadata = {
        "backend": settings.pskit_sequence_search_backend,
        "database_id": database.name,
        "database_path_hash": hashlib.sha256(str(database).encode("utf-8")).hexdigest()[:16],
        "hit_count": len(hits),
    }
    write_homology_summary(output_dir, hits, metadata)
    return {**run_result, "hits": hits, "hit_count": len(hits), "metadata": metadata}


def prepare_foldseek_query(input_path: Path, chain: str | None, output_dir: Path) -> Path:
    if not chain:
        return input_path
    file_format = "cif" if input_path.suffix.lower() in {".cif", ".mmcif"} else "pdb"
    structure = parse_structure(input_path, file_format)
    query_path = output_dir / f"query_chain_{chain}.pdb"
    save_selected(structure, query_path, ChainSelect(chain))
    structure_lines = query_path.read_text(encoding="utf-8").splitlines()
    if not any(line.startswith(("ATOM", "HETATM")) for line in structure_lines):
        raise TaskWorkerError(f"Structure chain does not exist or contains no atoms: {chain}")
    return query_path


def run_structure_homology_task(
    db: Session,
    user: User,
    task: Task,
    output_dir: Path,
) -> dict:
    args = task.input_json or {}
    input_path = resolve_owned_input_file(db, user, args)
    settings = get_settings()
    binary = resolve_configured_binary(settings.pskit_foldseek, "PSKIT_FOLDSEEK")
    database = require_database_path(settings.pskit_foldseek_db, "PSKIT_FOLDSEEK_DB")
    query_path = prepare_foldseek_query(input_path, args.get("chain"), output_dir)
    result_path = output_dir / "structure_homology_hits.tsv"
    temp_dir = output_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    command = build_foldseek_command(
        binary=binary,
        query_path=query_path,
        database_path=database,
        output_path=result_path,
        temp_dir=temp_dir,
        max_hits=int(args.get("max_hits", 50)),
        evalue=float(args.get("evalue", 1e-3)),
        sensitivity=float(args.get("sensitivity", 9.5)),
    )
    try:
        run_result = run_external_command(command, output_dir)
        hits = parse_homology_hits(result_path)
    finally:
        cleanup_scientific_scratch_dir(output_dir, temp_dir)
    metadata = {
        "backend": "foldseek",
        "database_id": database.name,
        "database_path_hash": hashlib.sha256(str(database).encode("utf-8")).hexdigest()[:16],
        "chain": args.get("chain"),
        "hit_count": len(hits),
    }
    write_homology_summary(output_dir, hits, metadata)
    return {**run_result, "hits": hits, "hit_count": len(hits), "metadata": metadata}


def persist_generated_candidates(
    db: Session,
    user: User,
    task: Task,
    *,
    track_name: str,
    generator_name: str,
    generator_version: str | None,
    result: dict,
) -> list[str]:
    args = task.input_json or {}
    raw_run_id = args.get("research_run_id")
    if not raw_run_id:
        raise TaskWorkerError("research_run_id is required for candidate generation")
    try:
        research_run_id = UUID(str(raw_run_id))
    except ValueError as exc:
        raise TaskWorkerError("research_run_id must be a valid UUID") from exc
    research_run = db.get(ResearchRun, research_run_id)
    if not research_run or research_run.user_id != user.id:
        raise TaskWorkerError("Research run not found or not owned by task user")
    candidate_track = db.scalar(
        select(CandidateTrack).where(
            CandidateTrack.research_run_id == research_run.id,
            CandidateTrack.track == track_name,
        )
    )
    if not candidate_track:
        raise TaskWorkerError(f"Candidate track is missing: {track_name}")
    try:
        link_task_to_research_run(
            db,
            user,
            research_run,
            task,
            "candidate_generation",
        )
    except ValueError as exc:
        raise TaskWorkerError(str(exc)) from exc
    iteration = int(args.get("iteration", candidate_track.current_iteration + 1))
    parameters_hash = str(args.get("parameters_hash") or "")
    if not parameters_hash:
        parameters_hash = hashlib.sha256(
            json.dumps(args, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
    candidate_ids: list[str] = []
    for item in result["candidates"]:
        sequence = str(item["sequence"]).upper()
        allowed = set("ACGU") if track_name == "rna" else set("ACDEFGHIKLMNPQRSTVWY")
        invalid = sorted(set(sequence) - allowed)
        if invalid:
            raise TaskWorkerError(
                f"{track_name} candidate contains unsupported symbols: {''.join(invalid)}"
            )
        existing = db.scalar(
            select(Candidate).where(
                Candidate.generation_task_id == task.id,
                Candidate.sequence == sequence,
            )
        )
        if existing:
            candidate_ids.append(str(existing.id))
            continue
        candidate = Candidate(
            research_run_id=research_run.id,
            candidate_track_id=candidate_track.id,
            user_id=user.id,
            track=track_name,
            sequence=sequence,
            sequence_length=len(sequence),
            generator_name=generator_name,
            generator_version=generator_version,
            generation_task_id=task.id,
            iteration=iteration,
            seed=args.get("seed"),
            parameters_hash=parameters_hash,
            raw_metrics_json=item.get("metrics") or {},
            metadata_json={
                "external_id": item.get("external_id"),
                "adapter_metadata": item.get("metadata") or {},
                "generator_metadata": result.get("metadata") or {},
                "target_hash": (research_run.metadata_json or {}).get(
                    "target_hash"
                ),
            },
        )
        db.add(candidate)
        db.flush()
        candidate_ids.append(str(candidate.id))
    candidate_track.current_iteration = max(candidate_track.current_iteration, iteration)
    candidate_track.status = "evaluating"
    candidate_track.summary_json = {
        **(candidate_track.summary_json or {}),
        "generation_task_id": str(task.id),
        "candidate_count": len(candidate_ids),
        "generator_name": generator_name,
        "generator_version": generator_version,
    }
    candidate_track.updated_at = now_utc()
    refresh_research_run_progress(db, research_run, allow_auto_advance=False)
    db.flush()
    return candidate_ids


def write_candidate_outputs(output_dir: Path, result: dict) -> None:
    (output_dir / "candidates.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    with (output_dir / "candidates.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["external_id", "sequence", "length", "metrics"],
        )
        writer.writeheader()
        for item in result["candidates"]:
            writer.writerow(
                {
                    "external_id": item.get("external_id") or "",
                    "sequence": item["sequence"],
                    "length": len(item["sequence"]),
                    "metrics": json.dumps(item.get("metrics") or {}, ensure_ascii=False),
                }
            )


def candidate_generator_version(metadata: dict) -> str | None:
    for key in ("server_version", "library_version", "model_version", "version"):
        value = metadata.get(key)
        if value is not None:
            return str(value)
    return None


def run_pepccd_mcp_candidate_task(
    db: Session,
    user: User,
    task: Task,
    output_dir: Path,
    lease_guard: Callable[[], None] | None = None,
) -> dict:
    args = task.input_json or {}
    raw_result = asyncio.run(call_pepccd_mcp(args))
    (output_dir / "pepccd_mcp_raw.json").write_text(
        json.dumps(raw_result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    try:
        result = normalize_candidate_result(raw_result, source_label="PepCCD MCP")
    except ScientificToolDependencyError as exc:
        raise TaskWorkerError(str(exc)) from exc
    write_candidate_outputs(output_dir, result)
    if lease_guard is not None:
        lease_guard()
    metadata = result.get("metadata") or {}
    candidate_ids = persist_generated_candidates(
        db,
        user,
        task,
        track_name="peptide",
        generator_name="PepCCD MCP",
        generator_version=candidate_generator_version(metadata),
        result=result,
    )
    return {
        "stdout": json.dumps(raw_result, ensure_ascii=False, default=str),
        "stderr": "",
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "track": "peptide",
        "generator_metadata": metadata,
    }


def run_coral_mcp_candidate_task(
    db: Session,
    user: User,
    task: Task,
    output_dir: Path,
    lease_guard: Callable[[], None] | None = None,
) -> dict:
    args = task.input_json or {}
    raw_result = asyncio.run(call_remote_rna_expert(args))
    (output_dir / "coral_mcp_raw.json").write_text(
        json.dumps(raw_result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    try:
        result = normalize_candidate_result(raw_result, source_label="CORAL RNA MCP")
    except ScientificToolDependencyError as exc:
        raise TaskWorkerError(str(exc)) from exc
    write_candidate_outputs(output_dir, result)
    if lease_guard is not None:
        lease_guard()
    metadata = result.get("metadata") or {}
    candidate_ids = persist_generated_candidates(
        db,
        user,
        task,
        track_name="rna",
        generator_name="CORAL RNA MCP",
        generator_version=candidate_generator_version(metadata),
        result=result,
    )
    return {
        "stdout": json.dumps(raw_result, ensure_ascii=False, default=str),
        "stderr": "",
        "candidate_ids": candidate_ids,
        "candidate_count": len(candidate_ids),
        "track": "rna",
        "generator_metadata": metadata,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_af3_research_context(
    db: Session,
    user: User,
    task: Task,
) -> None:
    args = task.input_json or {}
    raw_run_id = args.get("research_run_id")
    raw_candidate_id = args.get("candidate_id")
    if raw_run_id is None and raw_candidate_id is None:
        return
    if not raw_run_id or not raw_candidate_id:
        raise TaskWorkerError(
            "Research AF3 tasks require both research_run_id and candidate_id"
        )
    try:
        run_id = UUID(str(raw_run_id))
        candidate_id = UUID(str(raw_candidate_id))
    except ValueError as exc:
        raise TaskWorkerError("Invalid AF3 research context UUID") from exc
    research_run = db.get(ResearchRun, run_id)
    candidate = db.get(Candidate, candidate_id)
    link = db.scalar(
        select(ResearchTaskLink).where(
            ResearchTaskLink.task_id == task.id,
            ResearchTaskLink.research_run_id == run_id,
            ResearchTaskLink.candidate_id == candidate_id,
            ResearchTaskLink.user_id == user.id,
            ResearchTaskLink.role == "af3",
        )
    )
    if (
        research_run is None
        or research_run.user_id != user.id
        or candidate is None
        or candidate.user_id != user.id
        or candidate.research_run_id != research_run.id
        or link is None
    ):
        raise TaskWorkerError("AF3 task is not bound to the owned candidate and research run")
    target_hash = ensure_research_target_hash(research_run)
    candidate_hash = hashlib.sha256(candidate.sequence.encode("utf-8")).hexdigest()
    if args.get("target_hash") != target_hash:
        raise TaskWorkerError("AF3 task target hash no longer matches the research run")
    if args.get("candidate_sequence_hash") != candidate_hash:
        raise TaskWorkerError("AF3 task candidate hash no longer matches the candidate")
    entity_sequences = {
        "".join(str(item.get("sequence") or "").split()).upper()
        for item in args.get("entities") or []
        if isinstance(item, dict)
    }
    target_sequence = "".join(
        str(
            (research_run.target_json or {}).get("sequence")
            or (research_run.target_json or {}).get("protein_sequence")
            or ""
        ).split()
    ).upper()
    if target_sequence not in entity_sequences or candidate.sequence not in entity_sequences:
        raise TaskWorkerError("AF3 entities do not contain the bound target and candidate")


def run_alphafold3_task(
    db: Session,
    user: User,
    task: Task,
    output_dir: Path,
) -> dict:
    validate_af3_research_context(db, user, task)
    settings = get_settings()
    args = task.input_json or {}
    entities = args.get("entities") or []
    protein_sequences = args.get("protein_sequences") or []
    if not entities and not protein_sequences:
        raise TaskWorkerError("entities or protein_sequences are required")
    configured_gpu_device = str(settings.pskit_af3_gpu_device)
    requested_gpu_device = args.get("gpu_device")
    if requested_gpu_device is not None and str(requested_gpu_device) != configured_gpu_device:
        raise TaskWorkerError(
            "The requested AlphaFold3 GPU is not allowed by the server policy"
        )
    if configured_gpu_device not in settings.allowed_af3_gpu_devices:
        raise TaskWorkerError("The configured AlphaFold3 GPU is not in the server allowlist")
    gpu_device = configured_gpu_device
    af3_image = str(getattr(settings, "pskit_af3_image", "alphafold3:3.0.1"))
    af3_timeout = int(
        getattr(
            settings,
            "pskit_af3_timeout_seconds",
            getattr(settings, "task_subprocess_timeout_seconds", 3600),
        )
    )
    lock_root = Path(getattr(settings, "data_dir", output_dir.parent))
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
        "gpu_device": gpu_device,
        "max_template_date": str(args.get("max_template_date", "2021-09-30")),
    }
    (output_dir / "input").mkdir(parents=True, exist_ok=True)
    params_path = output_dir / "params.json"
    write_params(params_path, params)
    try:
        try:
            with af3_gpu_lock(lock_root, gpu_device):
                require_af3_gpu_memory_available(gpu_device)
                baseline = snapshot_active_af3_containers(af3_image)
                if baseline is None:
                    raise Af3GpuMemoryUnavailable(
                        "\u65e0\u6cd5\u53ef\u9760\u8bfb\u53d6 Docker \u4e2d\u7684 AlphaFold3 \u4efb\u52a1\u72b6\u6001\uff0c"
                        "\u4e3a\u907f\u514d\u540c GPU \u5e76\u53d1\u5df2\u62d2\u7edd\u672c\u6b21\u63d0\u4ea4"
                    )
                if baseline:
                    raise Af3GpuMemoryUnavailable(
                        f"AlphaFold3 GPU {gpu_device} 已有活动容器 "
                        f"({len(baseline)} 个)，请等待其结束后再重试"
                    )
                result = run_command(
                    [
                        legacy_python_executable(),
                        "-m",
                        "pskit.ai.run_pskit",
                        str(params_path),
                    ],
                    output_dir,
                    timeout_seconds=af3_timeout,
                    cleanup_process_group=True,
                    af3_container_image=af3_image,
                    af3_container_ids_before=baseline,
                )
        except TaskWorkerError as exc:
            classified = classify_af3_resource_failure(output_dir, exc)
            if classified is not exc:
                raise classified from exc
            raise
    finally:
        normalize_af3_output_tree(output_dir)
    raise_if_legacy_error(output_dir)
    structure_files = sorted(
        [
            path
            for suffix in ("*.cif", "*.mmcif", "*.pdb")
            for path in output_dir.rglob(suffix)
            if path.is_file() and path.stat().st_size > 0
        ]
    )
    result_files = sorted(
        path
        for path in output_dir.rglob("*.json")
        if path.is_file()
        and path.name not in {
            "params.json",
            "error.json",
            "af3_evidence_manifest.json",
        }
        and path.stat().st_size > 0
    )
    if not structure_files or not result_files:
        raise TaskWorkerError(
            "AlphaFold3 exited without a verifiable result: "
            f"structures={len(structure_files)}, result_json={len(result_files)}"
        )
    validated_structures: list[dict] = []
    for path in structure_files:
        file_format = "cif" if path.suffix.lower() in {".cif", ".mmcif"} else "pdb"
        try:
            structure = parse_structure(path, file_format)
            atom_count = sum(1 for _atom in structure.get_atoms())
        except Exception as exc:
            raise TaskWorkerError(
                f"AlphaFold3 structure output cannot be parsed: {path.name}"
            ) from exc
        if atom_count <= 0:
            raise TaskWorkerError(
                f"AlphaFold3 structure output contains no atoms: {path.name}"
            )
        validated_structures.append(
            {
                "path": str(path.relative_to(output_dir)),
                "sha256": file_sha256(path),
                "atom_count": atom_count,
            }
        )
    validated_results: list[dict] = []
    for path in result_files:
        try:
            parsed_result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if parsed_result in ({}, [], None):
            continue
        validated_results.append(
            {
                "path": str(path.relative_to(output_dir)),
                "sha256": file_sha256(path),
            }
        )
    if not validated_results:
        raise TaskWorkerError("AlphaFold3 produced no non-empty parseable result JSON")
    evidence_manifest = {
        "schema_version": "1.0",
        "task_id": str(task.id),
        "research_run_id": args.get("research_run_id"),
        "candidate_id": args.get("candidate_id"),
        "target_hash": args.get("target_hash"),
        "candidate_sequence_hash": args.get("candidate_sequence_hash"),
        "job_name": params["job_name"],
        "model_seed": params["model_seed"],
        "num_diffusion_samples": params["num_diffusion_samples"],
        "entity_sequence_hashes": [
            hashlib.sha256(
                "".join(str(item.get("sequence") or "").split())
                .upper()
                .encode("utf-8")
            ).hexdigest()
            for item in entities
            if isinstance(item, dict)
        ],
        "structures": validated_structures,
        "result_json": validated_results,
        "created_at": now_utc().isoformat(),
    }
    evidence_path = output_dir / "af3_evidence_manifest.json"
    evidence_path.write_text(
        json.dumps(evidence_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        **result,
        "af3_validation": {
            "structure_files": [
                str(path.relative_to(output_dir)) for path in structure_files
            ],
            "result_files": [
                str(path.relative_to(output_dir)) for path in result_files
            ],
            "evidence_manifest": evidence_path.name,
        },
    }


def normalize_af3_output_tree(output_dir: Path) -> None:
    """接管旧版 AF3 子容器产生的 root 文件，并移除非产物符号链接。"""
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if not callable(getuid) or not callable(getgid):
        raise TaskWorkerError("AF3 output normalization requires a POSIX worker")
    docker_path = shutil.which("docker")
    if not docker_path:
        raise TaskWorkerError("Docker is required to normalize AF3 outputs")
    command = build_af3_output_normalization_command(
        docker_binary=Path(docker_path),
        container_image=getattr(get_settings(), "pskit_af3_image", "alphafold3:3.0.1"),
        output_dir=output_dir.resolve(),
        worker_uid=getuid(),
        worker_gid=getgid(),
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TaskWorkerError(
            f"AF3 output normalization failed ({exc.__class__.__name__})"
        ) from exc
    if completed.returncode != 0:
        diagnostic = output_dir / "af3_output_normalization_stderr.log"
        diagnostic.write_bytes((completed.stderr or b"")[:1024 * 1024])
        raise TaskWorkerError(
            f"AF3 output normalization exited with code {completed.returncode}"
        )


async def call_remote_rna_expert(args: dict) -> dict:
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:
        raise TaskWorkerError(f"MCP Python SDK is not available: {exc}") from exc

    settings = get_settings()
    if not settings.remote_rna_expert_sse_url:
        raise TaskWorkerError("REMOTE_RNA_EXPERT_SSE_URL is not configured")
    timeout_seconds = int(getattr(settings, "task_mcp_timeout_seconds", 900))
    num_samples = int(args.get("num_samples", args.get("num_candidates", 3)))
    if num_samples < 1:
        raise TaskWorkerError("num_samples must be at least 1")
    payload = {
        "pdb_id": str(args.get("pdb_id") or "").upper(),
        "chain": str(args.get("chain") or "A"),
        "num_samples": num_samples,
    }
    if args.get("length") is not None:
        length = int(args["length"])
        if length < 1:
            raise TaskWorkerError("length must be at least 1")
        payload["length"] = length
    if not payload["pdb_id"]:
        raise TaskWorkerError("pdb_id is required")

    async def invoke():
        async with sse_client(settings.remote_rna_expert_sse_url) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                return await session.call_tool("generate_rna_for_protein", payload)

    try:
        result = await asyncio.wait_for(
            invoke(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise TaskWorkerError(
            f"CORAL MCP timed out after {timeout_seconds} seconds"
        ) from exc
    except Exception as exc:
        raise TaskWorkerError(
            safe_mcp_exception_message("CORAL MCP", exc)
        ) from exc

    if getattr(result, "isError", False):
        raise TaskWorkerError("CORAL MCP returned a tool-level error")
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
                raise TaskWorkerError("CORAL MCP returned non-JSON content")
            if isinstance(parsed, dict):
                return parsed
    raise TaskWorkerError("CORAL MCP returned no structured result")


async def call_pepccd_mcp(args: dict) -> object:
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except Exception as exc:
        raise TaskWorkerError(f"MCP Python SDK is not available: {exc}") from exc

    settings = get_settings()
    if not settings.pepccd_mcp_url:
        raise TaskWorkerError("PEPCCD_MCP_URL is not configured")
    timeout_seconds = int(getattr(settings, "task_mcp_timeout_seconds", 900))
    try:
        payload = build_pepccd_mcp_payload(args)
    except (MCPAdapterError, TypeError, ValueError) as exc:
        raise TaskWorkerError(str(exc)) from exc

    async def invoke():
        async with streamable_http_client(settings.pepccd_mcp_url) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                try:
                    tool_name = select_pepccd_mcp_tool_name(
                        tools.tools,
                        configured_name=settings.pepccd_mcp_tool_name,
                    )
                except MCPAdapterError as exc:
                    raise TaskWorkerError(str(exc)) from exc
                return await session.call_tool(tool_name, payload)

    try:
        result = await asyncio.wait_for(
            invoke(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError as exc:
        raise TaskWorkerError(
            f"PepCCD MCP timed out after {timeout_seconds} seconds"
        ) from exc
    except Exception as exc:
        raise TaskWorkerError(
            safe_mcp_exception_message("PepCCD MCP", exc)
        ) from exc

    try:
        return extract_mcp_result(result, source_label="PepCCD MCP")
    except MCPAdapterError as exc:
        raise TaskWorkerError(str(exc)) from exc


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
    output_root = output_dir.resolve()
    for path in sorted(output_dir.rglob("*")):
        if path.is_symlink():
            raise TaskWorkerError(
                f"任务输出包含禁止登记的符号链接：{path.name}"
            )
        if not path.is_file():
            continue
        resolved = path.resolve()
        if output_root not in resolved.parents:
            raise TaskWorkerError(
                f"任务输出越出隔离目录：{path.name}"
            )
        kind, mime_type = classify_artifact(path)
        artifact = register_local_artifact(
            db,
            user,
            task.session_id,
            task.id,
            path,
            kind=kind,
            mime_type=mime_type,
            commit=False,
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


def backfill_candidate_raw_artifact(
    db: Session,
    task: Task,
    artifacts: list[dict],
) -> None:
    if task.task_type not in {"generate_coral_candidates", "generate_pepccd_candidates"}:
        return
    raw_artifact = next(
        (
            item
            for item in artifacts
            if item.get("filename") == "candidates.json"
            and item.get("artifact_id")
        ),
        None,
    )
    if raw_artifact is None:
        raise TaskWorkerError("Candidate generation produced no registered candidates.json")
    artifact_id = UUID(str(raw_artifact["artifact_id"]))
    candidates = db.scalars(
        select(Candidate).where(Candidate.generation_task_id == task.id)
    ).all()
    if not candidates:
        raise TaskWorkerError("Candidate generation persisted no candidates")
    for candidate in candidates:
        candidate.raw_artifact_id = artifact_id
        candidate.updated_at = now_utc()


def candidate_task_statuses(db: Session, candidates: list[Candidate]) -> list[str]:
    statuses: list[str] = []
    for candidate in candidates:
        if candidate.af3_task_id is None:
            continue
        candidate_task = db.get(Task, candidate.af3_task_id)
        if candidate_task is not None:
            statuses.append(candidate_task.status)
    return statuses


def sync_research_task_state(
    db: Session,
    task: Task,
    artifacts: list[dict],
) -> None:
    link = db.scalar(select(ResearchTaskLink).where(ResearchTaskLink.task_id == task.id))
    if not link:
        return
    research_run = db.get(ResearchRun, link.research_run_id)
    if not research_run:
        raise TaskWorkerError("Linked research run is missing")
    if link.role == "candidate_generation" and task.status == "failed":
        track_name = "rna" if task.task_type == "generate_coral_candidates" else "peptide"
        candidate_track = db.scalar(
            select(CandidateTrack).where(
                CandidateTrack.research_run_id == research_run.id,
                CandidateTrack.track == track_name,
            )
        )
        if candidate_track:
            candidate_track.status = "failed"
            candidate_track.summary_json = {
                **(candidate_track.summary_json or {}),
                "generation_task_id": str(task.id),
                "generation_error_type": task.error_type,
                "generation_error": public_task_error_message(task),
            }
            candidate_track.updated_at = now_utc()
    elif link.role == "af3" and link.candidate_id:
        candidate = db.get(Candidate, link.candidate_id)
        if not candidate:
            raise TaskWorkerError("Linked AF3 candidate is missing")
        candidate.af3_status = task.status
        structure_artifact = next(
            (item for item in artifacts if item.get("kind") == "structure"),
            None,
        )
        result_artifact = next(
            (
                item
                for item in artifacts
                if item.get("filename") == "af3_evidence_manifest.json"
                and item.get("artifact_id")
            ),
            None,
        )
        if structure_artifact and structure_artifact.get("artifact_id"):
            candidate.af3_artifact_id = UUID(str(structure_artifact["artifact_id"]))
        if task.status == "succeeded" and (
            structure_artifact is None or result_artifact is None
        ):
            raise TaskWorkerError(
                "AF3 success requires both a structure and bound evidence manifest"
            )
        if result_artifact is not None:
            candidate.metadata_json = {
                **(candidate.metadata_json or {}),
                "af3_result_artifact_id": str(result_artifact["artifact_id"]),
            }
        candidate.updated_at = now_utc()
        candidate_track = db.get(CandidateTrack, candidate.candidate_track_id)
        selected = db.scalars(
            select(Candidate).where(
                Candidate.research_run_id == research_run.id,
                Candidate.track == candidate.track,
                Candidate.iteration == candidate.iteration,
                Candidate.selection_status == "selected",
                Candidate.af3_task_id.is_not(None),
            )
        ).all()
        statuses = candidate_task_statuses(db, list(selected))
        if (
            candidate_track
            and candidate_track.current_iteration == candidate.iteration
        ):
            if statuses and all(item in {"succeeded", "failed"} for item in statuses):
                candidate_track.status = (
                    "completed" if all(item == "succeeded" for item in statuses) else "failed"
                )
            else:
                candidate_track.status = "af3_running"
            candidate_track.summary_json = {
                **(candidate_track.summary_json or {}),
                "af3_status_counts": {
                    status_name: statuses.count(status_name)
                    for status_name in {"queued", "running", "succeeded", "failed"}
                },
            }
            candidate_track.updated_at = now_utc()
    refresh_research_run_progress(db, research_run)


def release_task_lease(
    db: Session,
    task_id: UUID,
    lease_token: str,
) -> None:
    lease = db.scalar(
        select(TaskExecutionLease).where(
            TaskExecutionLease.task_id == task_id,
            TaskExecutionLease.lease_token == lease_token,
        )
    )
    if lease is None:
        raise TaskLeaseLost("任务租约在提交终态前丢失")
    db.delete(lease)


def execute_task(
    db: Session,
    task: Task,
    lease_token: str,
    *,
    harness_wiring_context: Any | None = None,
) -> Task:
    user = db.get(User, task.user_id)
    if not user:
        raise TaskWorkerError("Task owner no longer exists")
    if task.session_id:
        session = db.get(AgentSession, task.session_id)
        if not session or session.user_id != user.id:
            raise TaskWorkerError("Task session is missing or not owned by task user")

    # wzf：只有显式写入 Task.input 的持久化 Harness 意图才允许进入旁路；
    # 缺失该字段的旧任务保持原有 ToolRunner 行为，避免一次开关变更影响存量。
    persisted_dispatch = (task.input_json or {}).get("_harness_dispatch")
    if persisted_dispatch is not None:
        decision = validate_persisted_dispatch(
            capability_id=task.task_type,
            user_id=task.user_id,
            session_id=task.session_id,
            persisted_dispatch=persisted_dispatch,
            context=harness_wiring_context,
        )
        if not decision.native:
            raise TaskWorkerError(
                f"Harness dispatch rejected: {decision.reason}"
            )

    output_dir = task_artifact_dir(user.id, task.id)
    fence_task_lease(db, task.id, lease_token)
    task.progress = 0.15
    task.updated_at = now_utc()
    db.commit()

    artifacts: list[dict] = []
    try:
        if task.task_type == "predict_binding_sites":
            run_result = run_binding_site_task(db, user, task, output_dir)
        elif task.task_type == "predict_interaction":
            run_result = run_interaction_task(task, output_dir)
        elif task.task_type == "extract_empirical_features":
            run_result = run_empirical_features_task(db, user, task, output_dir)
        elif task.task_type == "search_sequence_homologs":
            run_result = run_sequence_homology_task(task, output_dir)
        elif task.task_type == "search_structure_homologs":
            run_result = run_structure_homology_task(db, user, task, output_dir)
        elif task.task_type == "generate_coral_candidates":
            run_result = run_coral_mcp_candidate_task(
                db,
                user,
                task,
                output_dir,
                lambda: fence_task_lease(db, task.id, lease_token),
            )
        elif task.task_type == "generate_pepccd_candidates":
            run_result = run_pepccd_mcp_candidate_task(
                db,
                user,
                task,
                output_dir,
                lambda: fence_task_lease(db, task.id, lease_token),
            )
        elif task.task_type == "run_alphafold3":
            run_result = run_alphafold3_task(db, user, task, output_dir)
        elif task.task_type == "remote_rna_expert__generate_rna_for_protein":
            run_result = run_remote_rna_expert_task(task, output_dir)
        else:
            raise TaskWorkerError(f"Unsupported worker task type: {task.task_type}")

        fence_task_lease(db, task.id, lease_token)
        artifacts = register_task_outputs(db, user, task, output_dir)
        backfill_candidate_raw_artifact(db, task, artifacts)
        task.status = "succeeded"
        task.progress = 1.0
        structured_result = {
            key: value
            for key, value in run_result.items()
            if key not in {"stdout", "stderr"}
        }
        execution_metadata = {
            key: value
            for key, value in (task.output_json or {}).items()
            if key.startswith("_")
        }
        task.output_json = {
            **execution_metadata,
            "output_dir": str(output_dir),
            "artifacts": artifacts,
            "stdout_preview": run_result.get("stdout", "")[:2000],
            **structured_result,
        }
        task.error_type = None
        task.error_message = None
        # 科研状态同步属于成功事务的一部分；同步失败不能继续伪装为 succeeded。
        sync_research_task_state(db, task, artifacts)
        task.finished_at = now_utc()
        task.updated_at = now_utc()
        release_task_lease(db, task.id, lease_token)
    except Exception as exc:
        db.rollback()
        task = db.get(Task, task.id)
        user = db.get(User, user.id)
        if task is None or user is None:
            raise
        if isinstance(exc, TaskLeaseLost) or task.status != "running":
            logger.warning(
                "丢弃失效任务执行器结果 task_id=%s error=%s",
                task.id,
                exc.__class__.__name__,
            )
            return task
        try:
            fence_task_lease(db, task.id, lease_token)
        except TaskLeaseLost:
            db.rollback()
            logger.warning("任务失败结果未保存：租约已失效 task_id=%s", task.id)
            return db.get(Task, task.id) or task
        artifact_registration_error: str | None = None
        try:
            artifacts = register_task_outputs(db, user, task, output_dir)
        except Exception as artifact_exc:
            db.rollback()
            task = db.get(Task, task.id)
            user = db.get(User, user.id)
            if task is None or user is None:
                raise
            artifacts = []
            artifact_registration_error = str(artifact_exc)
            try:
                fence_task_lease(db, task.id, lease_token)
            except TaskLeaseLost:
                db.rollback()
                logger.warning(
                    "任务诊断产物登记后租约失效 task_id=%s",
                    task.id,
                )
                return db.get(Task, task.id) or task
        task.status = "failed"
        task.progress = 1.0
        task.error_type = exc.__class__.__name__
        task.error_message = str(exc)
        execution_metadata = {
            key: value
            for key, value in (task.output_json or {}).items()
            if key.startswith("_")
        }
        task.output_json = {
            **execution_metadata,
            "output_dir": str(output_dir),
            "artifacts": artifacts,
        }
        if artifact_registration_error:
            task.output_json["artifact_registration_error"] = artifact_registration_error
        try:
            sync_research_task_state(db, task, artifacts)
        except Exception as sync_exc:
            task.output_json = {
                **(task.output_json or {}),
                "research_sync_error": str(sync_exc),
            }
        task.finished_at = now_utc()
        task.updated_at = now_utc()
        release_task_lease(db, task.id, lease_token)
    db.commit()
    db.refresh(task)
    return task


def run_once(
    *,
    initialize: bool = True,
    harness_wiring_context: Any | None = None,
) -> bool:
    """领取并执行一次任务；Harness context 只能由受控启动层显式传递。"""

    if initialize:
        init_db()
    db = SessionLocal()
    try:
        task = claim_next_task(db)
        if not task:
            return False
        lease = db.get(TaskExecutionLease, task.id)
        if lease is None:
            raise TaskLeaseLost("Worker 领取任务后未找到执行租约")
        lease_token = lease.lease_token
        with TaskLeaseHeartbeat(task.id, lease_token):
            # wzf：execute_task 会先检查 Task.input_json 中已持久化的
            # _harness_dispatch；这里不从客户端 claims 或内存状态自行生成意图。
            task = execute_task(
                db,
                task,
                lease_token,
                harness_wiring_context=harness_wiring_context,
            )
        print(f"{task.id} {task.task_type} {task.status}")
        return True
    finally:
        db.close()


def _run_science_dependency_probe(
    name: str,
    command: list[str],
    *,
    timeout_seconds: float = 15,
) -> dict:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "name": name,
            "status": "fail",
            "detail": exc.__class__.__name__,
        }
    return {
        "name": name,
        "status": "ok" if completed.returncode == 0 else "fail",
        "detail": "available" if completed.returncode == 0 else f"exit {completed.returncode}",
    }


def _local_science_dependency_missing(settings) -> list[str]:
    missing: list[str] = []
    directories = {
        "PSKIT_LEGACY_ROOT": settings.pskit_legacy_root,
        "PSKIT_MODEL_PARAMETERS": settings.pskit_model_parameters,
        "PSKIT_AF3_DB_DIR": settings.pskit_af3_db_dir,
        "PSKIT_AF3_MODEL_DIR": settings.pskit_af3_model_dir,
    }
    executables = {
        "PSKIT_LEGACY_PYTHON": settings.pskit_legacy_python or Path(sys.executable),
        "PSKIT_FOLDSEEK": settings.pskit_foldseek,
        "PSKIT_DSSP": settings.pskit_dssp,
        "PSKIT_SEQUENCE_SEARCH_BINARY": settings.pskit_sequence_search_binary,
    }
    databases = {
        "PSKIT_FOLDSEEK_DB": settings.pskit_foldseek_db,
        "PSKIT_SEQUENCE_SEARCH_DB": settings.pskit_sequence_search_db,
    }
    for name, path in directories.items():
        if path is None or not path.exists():
            missing.append(name)
    for name, path in executables.items():
        if path is None or not (
            (path.is_file() and os.access(path, os.X_OK))
            or shutil.which(str(path))
        ):
            missing.append(name)
    for name, path in databases.items():
        if path is None or not (
            path.exists()
            or (
                path.parent.exists()
                and any(path.parent.glob(f"{path.name}*"))
            )
        ):
            missing.append(name)
    if not settings.remote_rna_expert_sse_url:
        missing.append("REMOTE_RNA_EXPERT_SSE_URL")
    if not settings.pepccd_mcp_url:
        missing.append("PEPCCD_MCP_URL")
    if shutil.which("docker") is None:
        missing.append("docker-cli")
    return missing


def _probe_science_dependencies(settings) -> dict:
    mcp_checks: list[dict] = []
    legacy_python = str(settings.pskit_legacy_python or sys.executable)
    legacy_root = str(settings.pskit_legacy_root or "")
    docker_checks: list[dict] = [
        _run_science_dependency_probe(
            "Legacy AI runtime",
            [
                legacy_python,
                "-c",
                (
                    "import sys; "
                    f"sys.path.insert(0, {legacy_root!r}); "
                    "import torch; "
                    "import pskit.ai.INABe; "
                    "import pskit.ai.PAIR; "
                    "print(torch.__version__)"
                ),
            ],
            timeout_seconds=60,
        )
    ]
    if shutil.which("docker") is not None:
        docker_checks.extend(
            [
                _run_science_dependency_probe(
                    "Docker daemon",
                    ["docker", "info", "--format", "{{.ServerVersion}}"],
                ),
                _run_science_dependency_probe(
                    "AlphaFold3 image",
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{.Id}}",
                        settings.pskit_af3_image,
                    ],
                ),
                _run_science_dependency_probe(
                    "AlphaFold3 GPU runtime",
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--pull",
                        "never",
                        "--gpus",
                        f"device={settings.pskit_af3_gpu_device}",
                        "--entrypoint",
                        "python",
                        settings.pskit_af3_image,
                        "-c",
                        "print('gpu-runtime-ok')",
                    ],
                    timeout_seconds=30,
                ),
            ]
        )
    try:
        from app.doctor.checks import (
            check_sse_mcp_endpoint,
            check_streamable_mcp_endpoint,
        )

        async def inspect_mcp_services() -> list[dict]:
            checks = []
            if settings.remote_rna_expert_sse_url:
                checks.append(
                    check_sse_mcp_endpoint(
                        "CORAL RNA MCP",
                        settings.remote_rna_expert_sse_url,
                    )
                )
            if settings.pepccd_mcp_url:
                checks.append(
                    check_streamable_mcp_endpoint(
                        "PepCCD MCP",
                        settings.pepccd_mcp_url,
                        configured_tool_name=settings.pepccd_mcp_tool_name,
                    )
                )
            return list(await asyncio.gather(*checks)) if checks else []

        mcp_checks = asyncio.run(inspect_mcp_services())
    except Exception as exc:
        logger.warning(
            "科学 Worker MCP 就绪探测失败 error_type=%s",
            exc.__class__.__name__,
        )
        mcp_checks = [
            {
                "name": "MCP services",
                "status": "fail",
                "detail": exc.__class__.__name__,
            }
        ]
    return {
        "mcp_checks": mcp_checks,
        "docker_checks": docker_checks,
    }


def _merge_science_dependency_summary(
    missing: list[str],
    checks: dict,
    *,
    probe_status: str,
    probe_checked_at: datetime | None,
) -> dict:
    mcp_checks = list(checks.get("mcp_checks") or [])
    docker_checks = list(checks.get("docker_checks") or [])
    combined_missing = list(missing)
    for item in mcp_checks:
        if item.get("status") != "ok":
            combined_missing.append(
                f"{item.get('name') or 'MCP service'} connectivity"
            )
    for item in docker_checks:
        if item.get("status") != "ok":
            combined_missing.append(str(item.get("name") or "Docker runtime"))
    if probe_status == "pending":
        combined_missing.append("science dependency probe pending")
    return {
        "science_ready": not combined_missing,
        "missing_dependencies": sorted(set(combined_missing)),
        "science_probe_status": probe_status,
        "science_probe_checked_at": (
            probe_checked_at.isoformat() if probe_checked_at is not None else None
        ),
        "science_probe_age_seconds": (
            round(max((now_utc() - probe_checked_at).total_seconds(), 0.0), 3)
            if probe_checked_at is not None
            else None
        ),
        "mcp_checks": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "detail": item.get("detail"),
            }
            for item in mcp_checks
        ],
        "docker_checks": [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "detail": item.get("detail"),
            }
            for item in docker_checks
        ],
    }


def science_dependency_summary() -> dict:
    """同步检查科研依赖；用于定向诊断，不在 Worker 主循环中直接调用。"""

    global _science_probe_cache
    settings = get_settings()
    missing = _local_science_dependency_missing(settings)
    with _science_probe_lock:
        cached = _science_probe_cache
        if cached is not None and time.monotonic() - cached[0] < 60:
            return _merge_science_dependency_summary(
                missing,
                cached[2],
                probe_status="fresh",
                probe_checked_at=cached[1],
            )
    checks = _probe_science_dependencies(settings)
    checked_at = now_utc()
    with _science_probe_lock:
        _science_probe_cache = (time.monotonic(), checked_at, checks)
    return _merge_science_dependency_summary(
        missing,
        checks,
        probe_status="fresh",
        probe_checked_at=checked_at,
    )


def cached_science_dependency_summary() -> dict:
    """只读取最近探测快照，绝不阻塞 Worker 存活心跳。"""

    settings = get_settings()
    missing = _local_science_dependency_missing(settings)
    with _science_probe_lock:
        cached = _science_probe_cache
        inflight = _science_probe_inflight
    if cached is None:
        return _merge_science_dependency_summary(
            missing,
            {},
            probe_status="pending",
            probe_checked_at=None,
        )
    age = time.monotonic() - cached[0]
    return _merge_science_dependency_summary(
        missing,
        cached[2],
        probe_status="refreshing" if inflight or age >= 60 else "fresh",
        probe_checked_at=cached[1],
    )


def persist_cached_science_dependency_summary(summary: dict | None = None) -> None:
    """把后台探针的最新完成时间和结果写回心跳，但不伪造新的存活时间。"""

    db = SessionLocal()
    try:
        heartbeat = db.get(ServiceHeartbeat, "task-worker")
        if heartbeat is None:
            return
        heartbeat.metadata_json = {
            **(heartbeat.metadata_json or {}),
            **(
                dict(summary)
                if summary is not None
                else cached_science_dependency_summary()
            ),
        }
        db.commit()
    finally:
        db.close()


def schedule_science_dependency_probe() -> bool:
    """在守护线程刷新深度依赖快照，避免 Docker/GPU/MCP 探测阻塞主循环。"""

    global _science_probe_inflight
    with _science_probe_lock:
        cached = _science_probe_cache
        if _science_probe_inflight or (
            cached is not None and time.monotonic() - cached[0] < 60
        ):
            return False
        _science_probe_inflight = True

    def refresh() -> None:
        global _science_probe_inflight
        try:
            summary = science_dependency_summary()
            # wzf：直接持久化本轮完成快照；数据库提交完成前保持 inflight，
            # 使 False 始终表示“探测与结果持久化均已结束”。
            persist_cached_science_dependency_summary(summary)
        except Exception as exc:
            logger.warning(
                "科学 Worker 后台就绪探测失败 error_type=%s",
                exc.__class__.__name__,
            )
        finally:
            with _science_probe_lock:
                _science_probe_inflight = False

    threading.Thread(
        target=refresh,
        name="pskit-science-readiness-probe",
        daemon=True,
    ).start()
    return True


def record_worker_heartbeat() -> None:
    db = SessionLocal()
    try:
        heartbeat = db.get(ServiceHeartbeat, "task-worker")
        if heartbeat is None:
            heartbeat = ServiceHeartbeat(name="task-worker")
            db.add(heartbeat)
        heartbeat.updated_at = now_utc()
        heartbeat.metadata_json = {
            "pid": os.getpid(),
            "mode": "forever",
            **cached_science_dependency_summary(),
        }
        db.commit()
    finally:
        db.close()
    # wzf：先独立提交轻量存活心跳，再异步探测 Docker、GPU 和 MCP；
    # 深探测最坏耗时不再占用 Worker 主循环或制造过期心跳。
    schedule_science_dependency_probe()


def run_forever(
    poll_seconds: float = 2.0,
    *,
    max_error_backoff_seconds: float = 30.0,
    harness_wiring_context: Any | None = None,
) -> None:
    init_db()
    consecutive_failures = 0
    while True:
        try:
            record_worker_heartbeat()
            did_work = run_once(
                initialize=False,
                harness_wiring_context=harness_wiring_context,
            )
        except Exception:
            consecutive_failures += 1
            # wzf：瞬时数据库或依赖异常不应让常驻 Worker 永久退出；心跳停止更新，
            # 就绪探针仍会如实转为失败，日志同时保留完整堆栈供排查。
            error_backoff = min(
                max_error_backoff_seconds,
                max(poll_seconds, 0.1)
                * (2 ** min(consecutive_failures - 1, 8)),
            )
            logger.exception(
                "Worker 主循环异常，将在 %.1f 秒后重试 consecutive_failures=%s",
                error_backoff,
                consecutive_failures,
            )
            time.sleep(error_backoff)
            continue
        consecutive_failures = 0
        if not did_work:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "forever":
        run_forever()
    else:
        run_once()
