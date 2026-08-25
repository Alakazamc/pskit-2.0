from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.db.models import Artifact, User
from app.db.session import get_db

router = APIRouter(prefix="/api/files", tags=["files"])


def local_artifact_path(object_key: str) -> Path:
    settings = get_settings()
    root = settings.artifact_dir.resolve()
    path = (root / object_key).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path")
    return path


@router.get("/{artifact_id}/download")
def download_artifact(
    artifact_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found")
    if artifact.storage_backend != "local":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Only local artifacts are implemented",
        )
    path = local_artifact_path(artifact.object_key)
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file missing")
    return FileResponse(path, filename=artifact.filename, media_type=artifact.mime_type)
