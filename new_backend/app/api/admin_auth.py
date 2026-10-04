"""JWT management authentication using live server-side privileges."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.auth import CurrentUserDep
from app.contracts.admin import AdminMe

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


async def get_admin_principal(user: CurrentUserDep, request: Request) -> AdminMe:
    """Verify identity first and load current permissions from the private database."""
    store = getattr(request.app.state, "admin_store", None)
    if user.is_anonymous is not False or store is None:
        raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
    principal = await run_in_threadpool(store.principal, user.id)
    if not principal.roles:
        raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
    return principal


AdminPrincipalDep = Annotated[AdminMe, Depends(get_admin_principal)]


def require_permission(permission: str, resource_owner=None):
    """Reject missing current privileges; service writes additionally check scope."""

    async def dependency(principal: AdminPrincipalDep, request: Request) -> AdminMe:
        if permission not in principal.permissions:
            raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
        resource = resource_owner(request) if callable(resource_owner) else resource_owner
        if (
            resource
            and "platform_admin" not in principal.roles
            and resource not in principal.service_ids
        ):
            raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
        return principal

    return dependency


@router.get("/me")
async def admin_me(principal: AdminPrincipalDep) -> AdminMe:
    """Return roles, allowed actions and owned service scopes, without secrets."""
    return principal
