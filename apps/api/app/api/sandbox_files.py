"""Public workspace operations accept authorized IDs, never Docker or filesystem paths."""

from mimetypes import guess_type
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.auth import CurrentUserDep
from app.contracts.catalog import ArtifactRef
from app.contracts.sandbox import WorkspaceFileRef
from app.domain.catalog import ContextNotFound
from app.ports.workspace_sandbox import WorkspaceProviderError

router = APIRouter(prefix="/api/v1/sandbox", tags=["sandbox files"])


class PrepareFiles(BaseModel):
    file_ids: list[str] = Field(max_length=10)


def transfer(request):
    service = getattr(request.app.state, "workspace_transfer", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Sandbox workspace is unavailable")
    return service


@router.post("/sessions/{session_id}/files", response_model=list[WorkspaceFileRef])
async def prepare(session_id: str, payload: PrepareFiles, user: CurrentUserDep, request: Request):
    try:
        return await transfer(request).prepare(user.id, session_id, payload.file_ids)
    except ContextNotFound as exc:
        raise HTTPException(status_code=404, detail="Session or file not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid workspace transfer") from exc
    except WorkspaceProviderError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": exc.code.value, "retryable": exc.retryable},
        ) from exc


@router.get("/artifacts", response_model=list[ArtifactRef])
async def artifacts(user: CurrentUserDep, request: Request):
    return transfer(request).artifacts.list(user.id)


@router.get("/artifacts/{artifact_id}/download")
async def download(artifact_id: str, user: CurrentUserDep, request: Request):
    stored = transfer(request).artifacts.read(user.id, artifact_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    name, raw = stored
    return Response(
        content=raw,
        media_type=guess_type(name)[0] or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )
