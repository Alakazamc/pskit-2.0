"""Review and publish LiteLLM aliases without exposing provider credentials."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.admin_auth import require_permission
from app.contracts.admin import AdminMe, AdminModel, AdminPage, ModelDraft, RevisionRequest
from app.domain.admin.roles import RevisionConflict

router = APIRouter(prefix="/api/v1/admin", tags=["admin-models"])
Read = Annotated[AdminMe, Depends(require_permission("models:read"))]
Write = Annotated[AdminMe, Depends(require_permission("models:write"))]
Publish = Annotated[AdminMe, Depends(require_permission("models:publish"))]


def management_error(exc):
    """Map domain conflicts to stable client codes without leaking endpoints."""
    status = (
        409 if isinstance(exc, RevisionConflict) else 404 if isinstance(exc, LookupError) else 422
    )
    return HTTPException(status, detail={"code": str(exc)})


@router.get("/llm-aliases")
async def list_aliases(
    actor: Read,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> AdminPage[AdminModel]:
    return await request.app.state.model_policy.admin_list(limit=limit, cursor=cursor)


@router.put("/llm-aliases/{alias:path}/draft")
async def save_alias(alias: str, payload: ModelDraft, actor: Write, request: Request) -> AdminModel:
    try:
        return await request.app.state.model_policy.save(
            actor.user_id, alias, payload, request_id=request.headers.get("x-request-id")
        )
    except (ValueError, LookupError) as exc:
        raise management_error(exc) from exc


@router.post("/llm-aliases/{alias:path}/publish")
async def publish_alias(
    alias: str, payload: RevisionRequest, actor: Publish, request: Request
) -> AdminModel:
    try:
        return await request.app.state.model_policy.publish(
            actor.user_id, alias, payload, request_id=request.headers.get("x-request-id")
        )
    except (ValueError, LookupError) as exc:
        raise management_error(exc) from exc


@router.post("/llm-aliases/{alias:path}/retire")
async def retire_alias(
    alias: str, payload: RevisionRequest, actor: Publish, request: Request
) -> AdminModel:
    try:
        return await request.app.state.model_policy.publish(
            actor.user_id,
            alias,
            payload,
            retire=True,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError) as exc:
        raise management_error(exc) from exc


@router.post("/llm-aliases/{alias:path}/qualify")
async def qualify_alias(
    alias: str, payload: RevisionRequest, actor: Publish, request: Request
) -> AdminModel:
    """Run only a host-configured budgeted probe; admin JSON cannot assert success."""
    qualifier = getattr(request.app.state, "model_qualifier", None)
    if qualifier is None:
        raise HTTPException(422, detail={"code": "MODEL_QUALIFIER_UNAVAILABLE"})
    try:
        return await request.app.state.model_policy.qualify(
            actor.user_id, alias, payload, qualifier,
            request_id=request.headers.get("x-request-id"),
        )
    except TimeoutError as exc:
        raise HTTPException(422, detail={"code": "MODEL_QUALIFICATION_TIMEOUT"}) from exc
    except PermissionError as exc:
        raise HTTPException(422, detail={"code": "MODEL_QUALIFICATION_BUDGET_EXHAUSTED"}) from exc
    except (ValueError, LookupError) as exc:
        raise management_error(exc) from exc
