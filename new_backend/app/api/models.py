"""Authenticated, secret-free model choices for the chat composer."""

from fastapi import APIRouter, Request

from app.api.auth import CurrentUserDep
from app.services.model_catalog import ModelOption

router = APIRouter(prefix="/api/v1", tags=["models"])


@router.get("/models")
async def list_models(user: CurrentUserDep, request: Request) -> list[ModelOption]:
    """Return gateway-visible model aliases with confirmed image support."""
    policy = getattr(request.app.state, 'model_policy', None)
    return (await policy.visible_for(user.id, 'chat') if policy and policy.managed
            else list(await request.app.state.model_catalog.list_models()))
