import hashlib
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from app.api.auth import CurrentUserDep
from app.api.capabilities import Af3Dep
from app.api.catalog import CatalogDep
from app.api.runs import ConversationStoreDep
from app.api.usage import QuotaDep
from app.contracts.capabilities import Af3JobRequest
from app.contracts.conversation import (
    ContextRef,
    Message,
    MessageRequest,
    Project,
    ProjectCreate,
    ProjectIconUpdate,
    ProjectRename,
    ProjectSkillSettings,
    RunRef,
    Session,
    SessionCreate,
    SessionMove,
    SessionRename,
)
from app.domain.catalog import ContextNotFound
from app.domain.conversation import IdempotencyConflict
from app.domain.project_routes import project_id_from_key
from app.domain.quota import GpuQuotaExceeded, TokenQuotaExceeded

router = APIRouter(prefix="/api/v1", tags=["workspace"])


def _project_id(
    key: str, user_id: str, conversations: ConversationStoreDep, *, allow_personal: bool = True,
) -> str:
    """Resolve an owned project route key or raise HTTP 404."""
    project_id = project_id_from_key(key)
    if (project_id is None or (not allow_personal and project_id == conversations.project_for(user_id).id)
            or not any(project.id == project_id for project in conversations.projects_for(user_id))):
        raise HTTPException(status_code=404, detail="Project not found")
    return project_id


def _require_chat_scope(
    request: Request, user_id: str, session_id: str, conversations: ConversationStoreDep,
) -> None:
    """Ensure the session belongs to the personal or project route scope."""
    key = request.path_params.get("project_key")
    expected = (_project_id(key, user_id, conversations, allow_personal=False) if key is not None
                else conversations.project_for(user_id).id)
    if conversations.project_id_for_session(user_id, session_id) != expected:
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/g")
async def list_projects(user: CurrentUserDep, conversations: ConversationStoreDep) -> list[Project]:
    """List projects belonging to the authenticated user."""
    return conversations.projects_for(user.id)


@router.post("/g", status_code=201)
async def create_project(
    payload: ProjectCreate, user: CurrentUserDep, conversations: ConversationStoreDep
) -> Project:
    """Create a project for the authenticated user."""
    return conversations.create_project(user.id, payload.name, payload.description, payload.icon)


@router.patch("/g/{project_key}/icon")
async def set_project_icon(
    project_key: str, payload: ProjectIconUpdate, user: CurrentUserDep,
    conversations: ConversationStoreDep,
) -> Project:
    """Update an owned project's icon."""
    project_id = _project_id(project_key, user.id, conversations)
    project = conversations.set_project_icon(user.id, project_id, payload.icon)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.patch("/g/{project_key}")
async def rename_project(
    project_key: str, payload: ProjectRename, user: CurrentUserDep,
    conversations: ConversationStoreDep,
) -> Project:
    """Rename an owned project."""
    project_id = _project_id(project_key, user.id, conversations)
    project = conversations.rename_project(user.id, project_id, payload.name)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.delete("/g/{project_key}", status_code=204)
async def archive_project(
    project_key: str, user: CurrentUserDep, conversations: ConversationStoreDep,
) -> None:
    """Archive an owned project."""
    project_id = _project_id(project_key, user.id, conversations)
    if not conversations.archive_project(user.id, project_id):
        raise HTTPException(status_code=404, detail="Project not found")


@router.get("/g/{project_key}/skills")
async def get_project_skills(
    project_key: str, user: CurrentUserDep, conversations: ConversationStoreDep,
    catalog: CatalogDep,
) -> ProjectSkillSettings:
    """Return the project's Skill selection filtered by current grants."""
    project_id = _project_id(project_key, user.id, conversations)
    settings = conversations.project_skill_settings(user.id, project_id)
    if settings is None:
        raise HTTPException(status_code=404, detail="Project not found")
    allowed = {skill.id for skill in catalog.skills_for(user.id)}
    return ProjectSkillSettings(
        skill_ids=[skill_id for skill_id in settings.skill_ids if skill_id in allowed],
        default_skill_ids=[skill_id for skill_id in settings.default_skill_ids if skill_id in allowed],
    )


@router.put("/g/{project_key}/skills")
async def set_project_skills(
    project_key: str, payload: ProjectSkillSettings, user: CurrentUserDep,
    conversations: ConversationStoreDep, catalog: CatalogDep,
) -> ProjectSkillSettings:
    """Validate and replace a project's allowed and default Skills."""
    project_id = _project_id(project_key, user.id, conversations)
    skill_ids = payload.skill_ids
    if (len(skill_ids) != len(set(skill_ids))
            or len(payload.default_skill_ids) != len(set(payload.default_skill_ids))
            or not set(payload.default_skill_ids).issubset(skill_ids)
            or not set(skill_ids).issubset({skill.id for skill in catalog.skills_for(user.id)})):
        raise HTTPException(status_code=422, detail="Invalid project skill selection")
    settings = conversations.set_project_skill_settings(user.id, project_id, payload)
    if settings is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return settings


@router.get("/c")
async def list_personal_chats(
    user: CurrentUserDep, conversations: ConversationStoreDep,
) -> list[Session]:
    """List sessions in the user's personal workspace."""
    return conversations.sessions_for(user.id, conversations.project_for(user.id).id) or []


@router.post("/c", status_code=201)
async def create_personal_chat(
    payload: SessionCreate, user: CurrentUserDep, conversations: ConversationStoreDep,
) -> Session:
    """Create a session in the user's personal workspace."""
    return conversations.create_session(user.id, conversations.project_for(user.id).id, payload.title)


@router.get("/g/{project_key}/c")
async def list_sessions(
    project_key: str, user: CurrentUserDep, conversations: ConversationStoreDep
) -> list[Session]:
    """List sessions within an owned project."""
    project_id = _project_id(project_key, user.id, conversations, allow_personal=False)
    sessions = conversations.sessions_for(user.id, project_id)
    if sessions is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return sessions


@router.post("/g/{project_key}/c", status_code=201)
async def create_session(
    project_key: str, payload: SessionCreate, user: CurrentUserDep,
    conversations: ConversationStoreDep,
) -> Session:
    """Create a session within an owned project."""
    project_id = _project_id(project_key, user.id, conversations, allow_personal=False)
    session = conversations.create_session(user.id, project_id, payload.title)
    if session is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return session


@router.get("/c/{session_id}")
@router.get("/g/{project_key}/c/{session_id}")
async def get_session(
    session_id: str, user: CurrentUserDep, conversations: ConversationStoreDep, request: Request,
) -> Session:
    """Read an owned session within its requested route scope."""
    _require_chat_scope(request, user.id, session_id, conversations)
    session = conversations.session_for(user.id, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/c/{session_id}")
@router.patch("/g/{project_key}/c/{session_id}")
async def rename_session(
    session_id: str, payload: SessionRename, user: CurrentUserDep,
    conversations: ConversationStoreDep, request: Request,
) -> Session:
    """Rename an owned session within its requested route scope."""
    _require_chat_scope(request, user.id, session_id, conversations)
    session = conversations.rename_session(user.id, session_id, payload.title)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.patch("/c/{session_id}/project")
@router.patch("/g/{project_key}/c/{session_id}/project")
async def move_session(
    session_id: str, payload: SessionMove, user: CurrentUserDep,
    conversations: ConversationStoreDep, request: Request,
) -> Session:
    """Move an owned session to another accessible project."""
    _require_chat_scope(request, user.id, session_id, conversations)
    session = conversations.move_session(user.id, session_id, payload.project_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session or target project not found")
    return session


@router.delete("/c/{session_id}", status_code=204)
@router.delete("/g/{project_key}/c/{session_id}", status_code=204)
async def archive_session(
    session_id: str, user: CurrentUserDep, conversations: ConversationStoreDep, request: Request,
) -> None:
    """Archive an owned session within its requested route scope."""
    _require_chat_scope(request, user.id, session_id, conversations)
    if not conversations.archive_session(user.id, session_id):
        raise HTTPException(status_code=404, detail="Session not found")


@router.get("/c/{session_id}/messages")
@router.get("/g/{project_key}/c/{session_id}/messages")
async def list_messages(
    session_id: str, user: CurrentUserDep, conversations: ConversationStoreDep, request: Request,
) -> list[Message]:
    """List messages in an owned session."""
    _require_chat_scope(request, user.id, session_id, conversations)
    messages = conversations.messages_for(user.id, session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return messages


@router.post("/c/{session_id}/messages")
@router.post("/g/{project_key}/c/{session_id}/messages")
async def send_message(
    session_id: str,
    payload: MessageRequest,
    user: CurrentUserDep,
    conversations: ConversationStoreDep,
    af3: Af3Dep,
    quotas: QuotaDep,
    catalog: CatalogDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=128)] = None,
) -> RunRef:
    """Validate context and quota, persist a message, and start an agent run."""
    _require_chat_scope(request, user.id, session_id, conversations)
    if conversations.messages_for(user.id, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    project_id = conversations.project_id_for_session(user.id, session_id)
    settings = conversations.project_skill_settings(user.id, project_id) if project_id else None
    if settings and settings.default_skill_ids:
        names = {skill.id: skill.name for skill in catalog.skills_for(user.id)}
        selected = {skill.id for skill in payload.skills}
        defaults = [ContextRef(id=skill_id, name=names[skill_id])
                    for skill_id in settings.default_skill_ids
                    if skill_id not in selected and skill_id in names]
        payload = payload.model_copy(update={"skills": defaults + payload.skills})
    fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    try:
        existing = conversations.existing_message_for(
            user.id, session_id, idempotency_key, fingerprint
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT"}) from exc
    if existing:
        return existing
    try:
        selected_model = await request.app.state.model_catalog.resolve(payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "MODEL_UNAVAILABLE"}) from exc
    if payload.reasoning_effort and payload.reasoning_effort not in selected_model.reasoning_levels:
        raise HTTPException(status_code=422, detail={"code": "MODEL_REASONING_UNAVAILABLE"})
    try:
        context = catalog.resolve_context(user.id, payload)
    except ContextNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "CONTEXT_NOT_FOUND"}) from exc
    if context.image_ids and not selected_model.supports_images:
        raise HTTPException(status_code=422,
                            detail={"code": "MODEL_DOES_NOT_SUPPORT_IMAGES"})
    payload = payload.model_copy(update={"attachments": list(context.attachments)})
    pi_mode = request.app.state.settings.agent_runtime == "pi"
    if pi_mode and request.app.state.agent_service is None:
        raise HTTPException(status_code=503, detail={"code": "PI_NOT_CONFIGURED"})
    if pi_mode and not request.app.state.agent_service.has_model_token(user.id):
        raise HTTPException(status_code=503, detail={"code": "MODEL_NOT_CONFIGURED"})
    wants_af3 = not pi_mode and ("af3" in payload.content.lower() or "alphafold" in payload.content.lower())
    if wants_af3:
        request.app.state.guest_capabilities.require_member(user.id)
    if wants_af3 and request.app.state.af3_executor == "disabled":
        raise HTTPException(status_code=503, detail={"code": "AF3_NOT_CONFIGURED"})
    if wants_af3 and not af3.can_submit(user.id, 20):
        raise HTTPException(
            status_code=409,
            detail={"code": "GPU_DAILY_QUOTA_EXCEEDED", "message": "Daily GPU quota exhausted"},
        )
    estimate = max(1, (len(context.user_prompt) + len(context.system_instructions)) // 4)
    estimate += len(context.image_ids) * 1024
    try:
        accepted = conversations.accept_message(
            user.id, session_id, payload, quotas, estimate, idempotency_key, fingerprint,
            waiting=wants_af3, instructions=context.system_instructions,
            allowed_tools=context.allowed_tools, user_prompt=context.user_prompt,
            model_id=selected_model.id,
            model_supports_images=selected_model.supports_images,
            reasoning_effort=payload.reasoning_effort,
            image_ids=context.image_ids,
        )
    except TokenQuotaExceeded as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "TOKEN_QUOTA_EXCEEDED", "message": "Monthly token quota exhausted"},
        ) from exc
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_CONFLICT"}) from exc
    if accepted is None:
        raise HTTPException(status_code=404, detail="Session not found")
    run, created = accepted
    if not created:
        return run
    if pi_mode:
        request.app.state.agent_service.schedule(user.id, session_id, run.run_id, context.user_prompt)
        return run
    if wants_af3:
        try:
            af3.submit(user.id, Af3JobRequest(run_id=run.run_id))
        except GpuQuotaExceeded as exc:
            raise HTTPException(status_code=409, detail={"code": "GPU_DAILY_QUOTA_EXCEEDED", "message": "Daily GPU quota exhausted"}) from exc
    return run
