"""Run-scoped internal API for controlled workspace tools used by Pi."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from app.contracts.workspace_tools import (
    FileEditRequest,
    FileEntryResponse,
    FileFindRequest,
    FileGrepRequest,
    FileListRequest,
    FileListResponse,
    FileReadRequest,
    FileReadResponse,
    FileSearchMatchResponse,
    FileSearchResponse,
    FileWriteRequest,
    FileWriteResponse,
    WorkspaceRequest,
)
from app.domain.internal_auth import WorkspaceToolClaims
from app.ports.workspace_sandbox import (
    WorkspaceCapabilities,
    WorkspaceErrorCode,
    WorkspaceProviderError,
)
from app.services.workspace_files import WorkspaceFiles, WorkspaceOperationContext

router = APIRouter(
    prefix="/internal/workspace",
    tags=["internal-workspace"],
    include_in_schema=False,
)


@dataclass(frozen=True, slots=True)
class AuthorizedWorkspace:
    context: WorkspaceOperationContext
    claims: WorkspaceToolClaims
    capabilities: WorkspaceCapabilities


def _provider_error(error: WorkspaceProviderError) -> HTTPException:
    status = {
        WorkspaceErrorCode.UNAVAILABLE: 503,
        WorkspaceErrorCode.UNSAFE_RUNTIME: 503,
        WorkspaceErrorCode.CONFLICT: 409,
        WorkspaceErrorCode.TIMEOUT: 504,
        WorkspaceErrorCode.CAPACITY: 429,
        WorkspaceErrorCode.NOT_FOUND: 404,
        WorkspaceErrorCode.INVALID_PATH: 422,
        WorkspaceErrorCode.CANCEL_UNCONFIRMED: 409,
    }.get(error.code, 503)
    return HTTPException(
        status_code=status,
        detail={"code": error.code.value, "retryable": error.retryable},
    )


async def _authorize(
    payload: WorkspaceRequest,
    request: Request,
    authorization: str | None,
) -> AuthorizedWorkspace:
    service = request.app.state.agent_service
    token = authorization.removeprefix("Bearer ") if authorization else ""
    claims = service.verify_workspace_tool_token(token) if service is not None else None
    if claims is None:
        raise HTTPException(status_code=401, detail={"code": "WORKSPACE_TOKEN_INVALID"})
    if (
        payload.run_id != claims.run_id
        or payload.session_id != claims.session_id
        or payload.attempt_id != claims.attempt_id
    ):
        raise HTTPException(status_code=403, detail={"code": "WORKSPACE_SCOPE_MISMATCH"})

    conversations = request.app.state.conversations
    status = conversations.run_status_for(claims.user_id, claims.run_id)
    if conversations.owner_for_run(claims.run_id) != claims.user_id:
        raise HTTPException(status_code=404, detail={"code": "WORKSPACE_RUN_NOT_FOUND"})
    if status is None or status.status != "running":
        raise HTTPException(status_code=409, detail={"code": "WORKSPACE_RUN_NOT_ACTIVE"})

    attempts = request.app.state.workspace_attempts
    attempt = attempts.get(claims.attempt_id) if attempts is not None else None
    if (
        attempt is None
        or attempt.user_id != claims.user_id
        or attempt.run_id != claims.run_id
        or attempt.session_id != claims.session_id
        or attempt.status not in {"queued", "running", "cancelling"}
    ):
        raise HTTPException(status_code=409, detail={"code": "WORKSPACE_ATTEMPT_NOT_ACTIVE"})

    owner = request.app.state.workspace_sandbox_store
    record = owner.get(claims.user_id) if owner is not None else None
    if record is None or record.lifecycle_state != "ready" or record.runtime_state != "running":
        raise HTTPException(status_code=503, detail={"code": "WORKSPACE_UNAVAILABLE"})
    try:
        capabilities = await request.app.state.workspace_sandbox_provider.capabilities()
    except WorkspaceProviderError as exc:
        raise _provider_error(exc) from exc
    if not capabilities.file_access:
        raise HTTPException(status_code=503, detail={"code": "WORKSPACE_FILE_ACCESS_UNAVAILABLE"})
    return AuthorizedWorkspace(
        context=WorkspaceOperationContext(
            user_id=claims.user_id,
            session_id=claims.session_id,
            attempt_id=claims.attempt_id,
        ),
        claims=claims,
        capabilities=capabilities,
    )


def _files(request: Request) -> WorkspaceFiles:
    service = request.app.state.workspace_files
    if service is None:
        raise HTTPException(status_code=503, detail={"code": "WORKSPACE_UNAVAILABLE"})
    return service


@router.post("/files/read")
async def read_file(
    payload: FileReadRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> FileReadResponse:
    authorized = await _authorize(payload, request, authorization)
    try:
        result = await _files(request).read(
            authorized.context, payload.path, offset=payload.offset, limit=payload.limit
        )
        binary = payload.encoding == "binary" or result.binary
        return FileReadResponse(
            path=result.path,
            content=(
                base64.b64encode(result.content).decode("ascii")
                if binary
                else result.content.decode("utf-8")
            ),
            encoding="base64" if binary else "utf-8",
            size=result.size,
            offset=result.offset,
            eof=result.eof,
            revision=result.revision,
        )
    except UnicodeDecodeError as exc:
        raise HTTPException(422, detail={"code": "WORKSPACE_TEXT_DECODE_FAILED"}) from exc
    except WorkspaceProviderError as exc:
        raise _provider_error(exc) from exc


@router.post("/files/write")
async def write_file(
    payload: FileWriteRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> FileWriteResponse:
    authorized = await _authorize(payload, request, authorization)
    try:
        content = (
            base64.b64decode(payload.content, validate=True)
            if payload.encoding == "base64"
            else payload.content.encode()
        )
        result = await _files(request).write(
            authorized.context,
            payload.path,
            content,
            expected_revision=payload.expected_revision,
        )
        return FileWriteResponse(path=result.path, size=result.size, revision=result.revision)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "WORKSPACE_CONTENT_INVALID"}) from exc
    except WorkspaceProviderError as exc:
        raise _provider_error(exc) from exc


@router.post("/files/edit")
async def edit_file(
    payload: FileEditRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> FileWriteResponse:
    authorized = await _authorize(payload, request, authorization)
    try:
        result = await _files(request).edit(
            authorized.context,
            payload.path,
            old_text=payload.old_text,
            new_text=payload.new_text,
            expected_revision=payload.expected_revision,
            replace_all=payload.replace_all,
        )
        return FileWriteResponse(path=result.path, size=result.size, revision=result.revision)
    except WorkspaceProviderError as exc:
        raise _provider_error(exc) from exc


@router.post("/files/list")
async def list_files(
    payload: FileListRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> FileListResponse:
    authorized = await _authorize(payload, request, authorization)
    try:
        entries = await _files(request).list(
            authorized.context,
            payload.path,
            max_depth=payload.max_depth,
            max_entries=payload.max_entries,
        )
        return FileListResponse(
            entries=[
                FileEntryResponse(
                    path=entry.path,
                    kind=entry.kind,
                    size=entry.size,
                    revision=entry.revision,
                )
                for entry in entries
            ]
        )
    except WorkspaceProviderError as exc:
        raise _provider_error(exc) from exc


@router.post("/files/find")
async def find_files(
    payload: FileFindRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> FileSearchResponse:
    authorized = await _authorize(payload, request, authorization)
    try:
        matches = await _files(request).find(
            authorized.context,
            payload.path,
            payload.pattern,
            max_depth=payload.max_depth,
            max_entries=payload.max_entries,
        )
        return FileSearchResponse(
            matches=[
                FileSearchMatchResponse(path=item.path, line=item.line, text=item.text)
                for item in matches
            ]
        )
    except WorkspaceProviderError as exc:
        raise _provider_error(exc) from exc


@router.post("/files/grep")
async def grep_files(
    payload: FileGrepRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> FileSearchResponse:
    authorized = await _authorize(payload, request, authorization)
    try:
        matches = await _files(request).grep(
            authorized.context,
            payload.path,
            payload.pattern,
            max_depth=payload.max_depth,
            max_matches=payload.max_matches,
            max_bytes=payload.max_bytes,
        )
        return FileSearchResponse(
            matches=[
                FileSearchMatchResponse(path=item.path, line=item.line, text=item.text)
                for item in matches
            ]
        )
    except WorkspaceProviderError as exc:
        raise _provider_error(exc) from exc
