from mimetypes import guess_type
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.api.auth import CurrentUserDep
from app.contracts.catalog import (
    ArtifactPreview,
    ArtifactRef,
    CatalogItem,
    FileRef,
    FileUploadRequest,
)
from app.domain.catalog import (
    IMAGE_MIME_TYPES,
    MAX_IMAGE_FILE_BYTES,
    MAX_PDF_FILE_BYTES,
    MAX_TEXT_FILE_BYTES,
    TEXT_FILE_SUFFIXES,
    CatalogStore,
    InvalidFileUpload,
)
from app.ports.providers import Af3Provider

router = APIRouter(prefix="/api/v1", tags=["catalog"])
MAX_ARTIFACT_PREVIEW_BYTES = 256 * 1024
PREVIEW_TEXT_SUFFIXES = TEXT_FILE_SUFFIXES | {".log"}


async def get_catalog(request: Request) -> CatalogStore:
    """Provide the configured Skill, resource, and file catalog."""
    return request.app.state.catalog


async def get_af3(request: Request) -> Af3Provider:
    """Provide the configured AF3 artifact provider."""
    return request.app.state.af3


CatalogDep = Annotated[CatalogStore, Depends(get_catalog)]
Af3Dep = Annotated[Af3Provider, Depends(get_af3)]


@router.get("/skills")
async def list_skills(user: CurrentUserDep, catalog: CatalogDep) -> list[CatalogItem]:
    """List Skills visible to the authenticated user."""
    return catalog.skills_for(user.id)


@router.get("/resources")
async def list_resources(user: CurrentUserDep, catalog: CatalogDep) -> list[CatalogItem]:
    """List resources visible to the authenticated user."""
    return catalog.resources_for(user.id)


@router.get("/files")
async def list_files(user: CurrentUserDep, catalog: CatalogDep) -> list[FileRef]:
    """List files belonging to the authenticated user."""
    return catalog.files_for(user.id)


@router.post("/files")
async def upload_file(
    payload: FileUploadRequest, user: CurrentUserDep, catalog: CatalogDep
) -> FileRef:
    """Create a text file from the JSON upload contract."""
    try:
        return catalog.add_file(user.id, payload)
    except InvalidFileUpload as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from exc


@router.put("/files/content")
async def upload_streamed_file(
    name: str,
    request: Request,
    user: CurrentUserDep,
    catalog: CatalogDep,
) -> FileRef:
    """Stream and validate a text, PDF, or image upload against storage limits."""
    from pathlib import Path

    suffix = Path(name).suffix.lower()
    if suffix != ".pdf" and suffix not in TEXT_FILE_SUFFIXES | IMAGE_MIME_TYPES.keys():
        raise HTTPException(status_code=415, detail={"code": "UNSUPPORTED_FILE_TYPE"})
    maximum = (
        MAX_PDF_FILE_BYTES
        if suffix == ".pdf"
        else MAX_IMAGE_FILE_BYTES
        if suffix in IMAGE_MIME_TYPES
        else MAX_TEXT_FILE_BYTES
    )
    single_limit = catalog.single_file_limit_for(user.id)
    if single_limit is not None:
        maximum = min(maximum, single_limit)
    total_limit = catalog.total_storage_limit_for(user.id)
    remaining = (
        None if total_limit is None else max(0, total_limit - catalog.stored_bytes_for(user.id))
    )
    chunks: list[bytes] = []
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > maximum:
                raise HTTPException(status_code=413, detail={"code": "FILE_TOO_LARGE"})
            if remaining is not None and size > remaining:
                raise HTTPException(status_code=413, detail={"code": "STORAGE_QUOTA_EXCEEDED"})
            chunks.append(chunk)
        raw = b"".join(chunks)
        if suffix == ".pdf":
            content = await request.app.state.pdf_processor.extract(raw)
            return catalog.add_parsed_pdf(user.id, name, raw, content)
        return catalog.add_uploaded_file(user.id, name, raw)
    except InvalidFileUpload as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from exc


@router.get("/files/{file_id}/download")
async def download_file(
    file_id: str,
    user: CurrentUserDep,
    catalog: CatalogDep,
) -> Response:
    """Download the user's file with safe response headers."""
    file = next((item for item in catalog.files_for(user.id) if item.id == file_id), None)
    content = catalog.file_bytes_for(user.id, file_id)
    if file is None or content is None:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(
        content=content,
        media_type=guess_type(file.name)[0] or "text/plain",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/files/{file_id}", status_code=204)
async def delete_file(file_id: str, user: CurrentUserDep, catalog: CatalogDep) -> None:
    """Delete one file belonging to the authenticated user."""
    if not catalog.delete_file(user.id, file_id):
        raise HTTPException(status_code=404, detail="File not found")


def _workspace_artifacts(request: Request):
    transfer = getattr(request.app.state, "workspace_transfer", None)
    return transfer.artifacts if transfer is not None else None


def _artifact_metadata(request: Request, user_id: str, af3, session_id: str | None = None):
    items = af3.artifacts_for(user_id) if session_id is None else af3.artifacts_for(user_id, session_id)
    workspace = _workspace_artifacts(request)
    if workspace is None:
        return items
    workspace_items = (workspace.list(user_id) if session_id is None
                       else workspace.list(user_id, session_id))
    return items + [item.model_dump() for item in workspace_items]


def _artifact_bytes(request: Request, user_id: str, artifact_id: str, af3):
    existing = af3.artifact_bytes_for(user_id, artifact_id)
    workspace = _workspace_artifacts(request)
    return existing or (workspace.read(user_id, artifact_id) if workspace else None)


@router.get("/artifacts")
async def list_artifacts(user: CurrentUserDep, af3: Af3Dep, request: Request) -> list[ArtifactRef]:
    """List AF3 artifact metadata visible to the user."""
    return [ArtifactRef(**artifact) for artifact in _artifact_metadata(request, user.id, af3)]


@router.get("/sessions/{session_id}/artifacts")
async def list_session_artifacts(
    session_id: str, user: CurrentUserDep, af3: Af3Dep, request: Request,
) -> list[ArtifactRef]:
    """List only artifacts from an active conversation owned by the caller."""
    if request.app.state.conversations.project_id_for_session(user.id, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return [ArtifactRef(**artifact) for artifact in
            _artifact_metadata(request, user.id, af3, session_id)]


@router.get("/artifacts/{artifact_id}/preview")
async def preview_artifact(
    artifact_id: str,
    user: CurrentUserDep,
    af3: Af3Dep,
    request: Request,
) -> ArtifactPreview:
    """Decode a small text artifact for an in-app preview."""
    metadata = next(
        (item for item in _artifact_metadata(request, user.id, af3) if item["id"] == artifact_id),
        None,
    )
    if metadata is None or not metadata["available"]:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if Path(metadata["name"]).suffix.lower() not in PREVIEW_TEXT_SUFFIXES:
        raise HTTPException(status_code=415, detail={"code": "ARTIFACT_PREVIEW_UNSUPPORTED"})
    if metadata["size"] is None or metadata["size"] > MAX_ARTIFACT_PREVIEW_BYTES:
        raise HTTPException(status_code=413, detail={"code": "ARTIFACT_PREVIEW_TOO_LARGE"})
    stored = _artifact_bytes(request, user.id, artifact_id, af3)
    if stored is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    name, content = stored
    if len(content) > MAX_ARTIFACT_PREVIEW_BYTES:
        raise HTTPException(status_code=413, detail={"code": "ARTIFACT_PREVIEW_TOO_LARGE"})
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=415, detail={"code": "ARTIFACT_PREVIEW_UNSUPPORTED"}
        ) from exc
    if "\x00" in text:
        raise HTTPException(status_code=415, detail={"code": "ARTIFACT_PREVIEW_UNSUPPORTED"})
    return ArtifactPreview(id=artifact_id, name=name, kind=metadata["kind"], text=text)


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: str,
    user: CurrentUserDep,
    af3: Af3Dep,
    request: Request,
) -> Response:
    """Download an owned AF3 artifact with safe response headers."""
    stored = _artifact_bytes(request, user.id, artifact_id, af3)
    if stored is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    name, content = stored
    return Response(
        content=content,
        media_type=guess_type(name)[0] or "application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
        },
    )
