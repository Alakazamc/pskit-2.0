"""Authenticated profile writes; the browser never receives Supabase server keys."""

import asyncio
import logging
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials

from app.adapters.live.supabase_auth import AuthRateLimited, InvalidCredentials
from app.api.auth import CurrentUserDep, bearer
from app.contracts.models import ProfileUpdateRequest, UserIdentity
from app.ports.providers import ProviderUnavailable
from app.services.avatars import MAX_AVATAR_BYTES, normalize_avatar

router = APIRouter(prefix="/api/v1/me", tags=["profile"])
Credentials = Annotated[HTTPAuthorizationCredentials, Depends(bearer)]


async def update_metadata(request: Request, token: str, metadata: dict[str, object]) -> UserIdentity:
    """Write current-user metadata and translate provider failures safely."""
    try:
        result = await request.app.state.identity_provider.update_profile(token, metadata)
    except AuthRateLimited as exc:
        raise HTTPException(429, {"code": "AUTH_RATE_LIMITED"},
                            headers={"Retry-After": exc.retry_after} if exc.retry_after else None) from exc
    except InvalidCredentials as exc:
        raise HTTPException(401, "Invalid credentials") from exc
    except ProviderUnavailable as exc:
        raise HTTPException(503, {"code": "PROFILE_UNAVAILABLE"}) from exc
    if result is None:
        raise HTTPException(401, "Invalid credentials")
    return result


@router.patch("")
async def update_profile(
    payload: ProfileUpdateRequest, user: CurrentUserDep,
    credentials: Credentials, request: Request,
) -> UserIdentity:
    """Change only the authenticated user's nickname, preserving identity and quota."""
    return await update_metadata(request, credentials.credentials, {"full_name": payload.name})


def avatar_key(user: UserIdentity, revision: str) -> str:
    """Build an owner-bound path exclusively from verified identity metadata."""
    return f"{user.id}/{revision}.webp"


async def clean_old_avatar(request: Request, user: UserIdentity) -> None:
    """Keep a successful profile write usable even when obsolete-object cleanup fails."""
    if user.avatar_revision:
        try:
            await request.app.state.avatar_storage.delete(avatar_key(user, user.avatar_revision))
        except ProviderUnavailable:
            logging.getLogger(__name__).warning("Obsolete avatar cleanup deferred")


@router.put("/avatar")
async def upload_avatar(
    user: CurrentUserDep, credentials: Credentials, request: Request,
) -> UserIdentity:
    """Validate and normalize a bounded raster image before saving the user's avatar."""
    if request.headers.get("content-type", "").split(";")[0] not in {
        "image/png", "image/jpeg", "image/webp",
    }:
        raise HTTPException(415, {"code": "AVATAR_FORMAT_UNSUPPORTED"})
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_AVATAR_BYTES:
            raise HTTPException(413, {"code": "AVATAR_TOO_LARGE"})
        body.extend(chunk)
    async with request.app.state.avatar_processing:
        try:
            image = await asyncio.to_thread(normalize_avatar, bytes(body))
        except ValueError as exc:
            raise HTTPException(422, {"code": "AVATAR_INVALID"}) from exc
    revision = uuid4().hex
    try:
        await request.app.state.avatar_storage.put(avatar_key(user, revision), image)
    except ProviderUnavailable as exc:
        raise HTTPException(503, {"code": "AVATAR_STORAGE_UNAVAILABLE"}) from exc
    # If Auth's response is lost, retain the uploaded object: the metadata update
    # may have succeeded. Deleting it on an ambiguous failure would break that avatar.
    updated = await update_metadata(request, credentials.credentials, {"pskit_avatar_revision": revision})
    await clean_old_avatar(request, user)
    return updated


@router.get("/avatar", response_class=Response, responses={200: {"content": {"image/webp": {}}}})
async def get_avatar(user: CurrentUserDep, request: Request) -> Response:
    """Return only the authenticated user's private image through Python."""
    if not user.avatar_revision:
        raise HTTPException(404, {"code": "AVATAR_NOT_FOUND"})
    try:
        content = await request.app.state.avatar_storage.get(avatar_key(user, user.avatar_revision))
    except ProviderUnavailable as exc:
        raise HTTPException(503, {"code": "AVATAR_STORAGE_UNAVAILABLE"}) from exc
    if content is None:
        raise HTTPException(404, {"code": "AVATAR_NOT_FOUND"})
    return Response(content, media_type="image/webp", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.delete("/avatar")
async def delete_avatar(
    user: CurrentUserDep, credentials: Credentials, request: Request,
) -> UserIdentity:
    """Remove the profile reference first, then discard its obsolete object."""
    updated = await update_metadata(request, credentials.credentials, {"pskit_avatar_revision": None})
    await clean_old_avatar(request, user)
    return updated
