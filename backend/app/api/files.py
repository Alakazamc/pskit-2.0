from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.artifacts.service import resolve_owned_local_artifact_reference
from app.auth.dependencies import get_current_user
from app.db.models import Artifact, User
from app.db.session import get_db


router = APIRouter(prefix="/api/files", tags=["files"])


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
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Only local artifacts are implemented")
    try:
        path = resolve_owned_local_artifact_reference(
            db,
            user,
            artifact_id=str(artifact.id),
        )
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file missing")
    return FileResponse(path, filename=artifact.filename, media_type=artifact.mime_type)
