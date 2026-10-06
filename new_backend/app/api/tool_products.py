"""Secret-free public Tool Product discovery endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import CurrentUserDep
from app.contracts.tool_products import PublishedToolProduct, ToolProductPage
from app.domain.tool_products.registry import (
    ToolProductNotAvailable,
    ToolProductRegistry,
)

router = APIRouter(prefix="/api/v1/tool-products", tags=["tool-products"])


def get_registry(request: Request) -> ToolProductRegistry:
    registry = getattr(request.app.state, "tool_product_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=503, detail={"code": "TOOL_PRODUCTS_UNAVAILABLE"}
        )
    return registry


RegistryDep = Annotated[ToolProductRegistry, Depends(get_registry)]


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

