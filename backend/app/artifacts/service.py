from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Artifact, User


def session_artifact_dir(user_id: UUID, session_id: UUID, tool_call_id: str) -> Path:
    settings = get_settings()
    root = settings.artifact_dir / str(user_id) / str(session_id) / tool_call_id
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def task_artifact_dir(user_id: UUID, task_id: UUID) -> Path:
    settings = get_settings()
    root = settings.artifact_dir / str(user_id) / "tasks" / str(task_id)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def register_local_artifact(
    db: Session,
    user: User,
    session_id: UUID | None,
    task_id: UUID | None,
    path: Path,
    kind: str,
    mime_type: str | None = None,
) -> Artifact:
    settings = get_settings()
    root = settings.artifact_dir.resolve()
    resolved = path.resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"Artifact path is outside artifact root: {resolved}")
    artifact = Artifact(
        user_id=user.id,
        session_id=session_id,
        task_id=task_id,
        kind=kind,
        storage_backend="local",
        object_key=str(resolved.relative_to(root)),
        filename=path.name,
        mime_type=mime_type,
        size_bytes=path.stat().st_size if path.exists() else None,
        metadata_json={},
    )
    db.add(artifact)
    db.commit()
    db.refresh(artifact)
    return artifact


def resolve_owned_local_artifact(db: Session, user: User, artifact_id: str) -> Path:
    settings = get_settings()
    artifact = db.get(Artifact, UUID(str(artifact_id)))
    if not artifact or artifact.user_id != user.id:
        raise FileNotFoundError("Artifact not found")
    if artifact.storage_backend != "local":
        raise NotImplementedError("Only local artifacts are implemented")
    root = settings.artifact_dir.resolve()
    path = (root / artifact.object_key).resolve()
    if root not in path.parents and path != root:
        raise ValueError("Invalid artifact path")
    if not path.exists() or not path.is_file():
        raise FileNotFoundError("Artifact file missing")
    return path
