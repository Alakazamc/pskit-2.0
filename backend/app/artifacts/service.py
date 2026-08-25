from pathlib import Path
import hashlib
import os
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Artifact, User


SAFE_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def safe_artifact_component(value: object, *, prefix: str) -> str:
    """把外部标识映射为稳定且不可穿越目录的单个路径段。"""
    raw = str(value or "").strip()
    if raw and raw not in {".", ".."} and SAFE_PATH_COMPONENT.fullmatch(raw):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def create_artifact_subdir(root: Path, *components: str) -> Path:
    resolved_root = root.resolve()
    resolved = resolved_root.joinpath(*components).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("Artifact directory escaped the configured artifact root")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def session_artifact_dir(user_id: UUID, session_id: UUID, tool_call_id: str) -> Path:
    settings = get_settings()
    component = safe_artifact_component(tool_call_id, prefix="call")
    return create_artifact_subdir(
        settings.artifact_dir,
        str(user_id),
        str(session_id),
        component,
    )


def task_artifact_dir(user_id: UUID, task_id: UUID) -> Path:
    settings = get_settings()
    return create_artifact_subdir(
        settings.artifact_dir,
        str(user_id),
        "tasks",
        str(task_id),
    )


def research_artifact_dir(user_id: UUID, research_run_id: UUID, operation_id: str) -> Path:
    settings = get_settings()
    component = safe_artifact_component(operation_id, prefix="operation")
    return create_artifact_subdir(
        settings.artifact_dir,
        str(user_id),
        "research-runs",
        str(research_run_id),
        component,
    )


def register_local_artifact(
    db: Session,
    user: User,
    session_id: UUID | None,
    task_id: UUID | None,
    path: Path,
    kind: str,
    mime_type: str | None = None,
    *,
    commit: bool = True,
) -> Artifact:
    settings = get_settings()
    root = settings.artifact_dir.resolve()
    lexical_path = Path(os.path.abspath(path))
    try:
        relative = lexical_path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Artifact path is outside artifact root: {lexical_path}") from exc
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("Artifact path must not contain symbolic links")
    resolved = lexical_path.resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"Artifact path is outside artifact root: {resolved}")
    if not resolved.is_file():
        raise ValueError("Artifact path must reference an existing regular file")
    object_key = str(resolved.relative_to(root))
    existing = db.scalar(
        select(Artifact).where(
            Artifact.user_id == user.id,
            Artifact.storage_backend == "local",
            Artifact.object_key == object_key,
        )
    )
    if existing is not None:
        existing.session_id = session_id
        existing.task_id = task_id
        existing.kind = kind
        existing.filename = path.name
        existing.mime_type = mime_type
        existing.size_bytes = resolved.stat().st_size
        if commit:
            db.commit()
            db.refresh(existing)
        else:
            db.flush()
        return existing
    artifact = Artifact(
        user_id=user.id,
        session_id=session_id,
        task_id=task_id,
        kind=kind,
        storage_backend="local",
        object_key=object_key,
        filename=path.name,
        mime_type=mime_type,
        size_bytes=resolved.stat().st_size,
        metadata_json={},
    )
    db.add(artifact)
    if commit:
        db.commit()
        db.refresh(artifact)
    else:
        db.flush()
    return artifact


def _resolve_registered_artifact_path(artifact: Artifact) -> Path:
    settings = get_settings()
    if artifact.storage_backend != "local":
        raise NotImplementedError("Only local artifacts are implemented")
    root = settings.artifact_dir.resolve()
    object_key = Path(artifact.object_key)
    if object_key.is_absolute():
        raise ValueError("Invalid artifact path")
    lexical_path = Path(os.path.abspath(root / object_key))
    try:
        relative = lexical_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Invalid artifact path") from exc

    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("Artifact path must not contain symbolic links")
    path = lexical_path.resolve()
    if root not in path.parents or not path.exists() or not path.is_file():
        raise FileNotFoundError("Artifact file missing")
    return path


def resolve_owned_local_artifact_reference(
    db: Session,
    user: User,
    *,
    artifact_id: str | None = None,
    file_path: str | None = None,
) -> Path:
    """只解析当前用户已登记的本地产物，路径参数不能绕过数据库所有权。"""

    artifact: Artifact | None = None
    if artifact_id:
        try:
            parsed_id = UUID(str(artifact_id))
        except ValueError as exc:
            raise FileNotFoundError("Artifact not found") from exc
        artifact = db.get(Artifact, parsed_id)
    elif file_path:
        root = get_settings().artifact_dir.resolve()
        raw_path = Path(file_path).expanduser()
        candidate = raw_path if raw_path.is_absolute() else root / raw_path
        lexical_path = Path(os.path.abspath(candidate))
        try:
            object_key = str(lexical_path.relative_to(root))
        except ValueError as exc:
            raise FileNotFoundError("Artifact not found") from exc
        artifact = db.scalar(
            select(Artifact).where(
                Artifact.user_id == user.id,
                Artifact.storage_backend == "local",
                Artifact.object_key == object_key,
            )
        )
    else:
        raise FileNotFoundError("Artifact not found")

    if artifact is None or artifact.user_id != user.id:
        raise FileNotFoundError("Artifact not found")
    return _resolve_registered_artifact_path(artifact)


def resolve_owned_local_artifact(db: Session, user: User, artifact_id: str) -> Path:
    return resolve_owned_local_artifact_reference(
        db,
        user,
        artifact_id=artifact_id,
    )
