"""Secret-free public Tool Product discovery endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import CurrentUserDep
from app.contracts.tool_products import (
    PublishedToolProduct,
    ToolProductPage,
    ToolRunHandoffContext,
    ToolRunSnapshot,
)
from app.domain.tool_products.registry import (
    ToolProductNotAvailable,
    ToolProductRegistry,
)

router = APIRouter(prefix="/api/v1/tool-products", tags=["tool-products"])
tool_run_router = APIRouter(prefix="/api/v1/tool-runs", tags=["tool-product-runs"])


class ToolRunStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict = Field(default_factory=dict)


def get_registry(request: Request) -> ToolProductRegistry:
    registry = getattr(request.app.state, "tool_product_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=503, detail={"code": "TOOL_PRODUCTS_UNAVAILABLE"}
        )
    return registry


RegistryDep = Annotated[ToolProductRegistry, Depends(get_registry)]


def get_run_gateway(request: Request):
    gateway = getattr(request.app.state, "tool_run_gateway", None)
    if gateway is None:
        raise HTTPException(503, detail={"code": "TOOL_RUNS_UNAVAILABLE"})
    return gateway


def run_error(exc: Exception) -> HTTPException:
    code = str(exc)
    if isinstance(exc, (LookupError, ToolProductNotAvailable)):
        status = 404
    elif code.endswith("QUOTA_EXCEEDED"):
        status = 429
    elif code.startswith("INVALID"):
        status = 422
    else:
        status = 409
    return HTTPException(status, detail={"code": code})


@router.get("")
def list_tool_products(
    user: CurrentUserDep,
    registry: RegistryDep,
    cursor: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ToolProductPage:
    """List immutable releases visible to the current member or guest."""
    actor_id = None if user.is_anonymous else user.id
    return registry.list_visible(actor_id, cursor, limit)


@router.get("/{slug}")
def get_tool_product(
    slug: str,
    user: CurrentUserDep,
    registry: RegistryDep,
) -> PublishedToolProduct:
    """Resolve one currently published product without exposing its MCP binding."""
    actor_id = None if user.is_anonymous else user.id
    try:
        return registry.resolve(slug, actor_id)
    except ToolProductNotAvailable as exc:
        raise HTTPException(
            status_code=404, detail={"code": "TOOL_PRODUCT_NOT_AVAILABLE"}
        ) from exc


@router.post("/{slug}/actions/{action_id}/runs")
def start_tool_run(
    slug: str,
    action_id: str,
    payload: ToolRunStartRequest,
    user: CurrentUserDep,
    request: Request,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=200)
    ],
) -> ToolRunSnapshot:
    policy = getattr(request.app.state, "guest_capabilities", None)
    if policy is not None:
        policy.require_member(user.id)
    try:
        return get_run_gateway(request).start_by_slug(
            slug, action_id, payload.arguments, user.id, idempotency_key
        )
    except (ValueError, LookupError, ToolProductNotAvailable) as exc:
        raise run_error(exc) from exc


@router.get("/{slug}/runs")
def tool_run_history(
    slug: str,
    user: CurrentUserDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
) -> list[ToolRunSnapshot]:
    return get_run_gateway(request).history(user.id, slug, limit)


@tool_run_router.get("/{run_id}")
def get_tool_run(
    run_id: str, user: CurrentUserDep, request: Request
) -> ToolRunSnapshot:
    run = get_run_gateway(request).get(user.id, run_id)
    if run is None:
        raise HTTPException(404, detail={"code": "TOOL_RUN_NOT_FOUND"})
    return run


@tool_run_router.post("/{run_id}/cancel")
def cancel_tool_run(
    run_id: str, user: CurrentUserDep, request: Request
) -> ToolRunSnapshot:
    try:
        return get_run_gateway(request).cancel(user.id, run_id)
    except (ValueError, LookupError) as exc:
        raise run_error(exc) from exc


@tool_run_router.post("/{run_id}/agent-handoffs/{handoff_id}")
def handoff_tool_run(
    run_id: str, handoff_id: str, user: CurrentUserDep, request: Request
) -> ToolRunHandoffContext:
    try:
        return get_run_gateway(request).handoff(user.id, run_id, handoff_id)
    except (ValueError, LookupError) as exc:
        raise run_error(exc) from exc
