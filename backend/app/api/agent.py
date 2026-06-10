import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.agent.orchestrator import AgentRuntime, LangGraphAgentRunner
from app.db.models import AgentMessage, AgentSession, User, now_utc
from app.db.session import get_db
from app.schemas.agent import AgentMessageRequest, AgentMessageResponse, AgentSessionResponse, CreateAgentSessionRequest


router = APIRouter(prefix="/api/agent", tags=["agent"])


def serialize_session(session: AgentSession) -> AgentSessionResponse:
    return AgentSessionResponse(
        id=str(session.id),
        title=session.title,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


def serialize_message(message: AgentMessage) -> AgentMessageResponse:
    return AgentMessageResponse(
        id=str(message.id),
        role=message.role,
        content=message.content,
        created_at=message.created_at.isoformat(),
        metadata=message.metadata_json,
    )


def load_owned_session(db: Session, user: User, session_id: UUID) -> AgentSession:
    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id or session.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


@router.post("/sessions", response_model=AgentSessionResponse)
def create_session(
    payload: CreateAgentSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = AgentSession(user_id=user.id, title=payload.title or "New Chat")
    db.add(session)
    db.commit()
    db.refresh(session)
    return serialize_session(session)


@router.get("/sessions", response_model=list[AgentSessionResponse])
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sessions = db.scalars(
        select(AgentSession)
        .where(AgentSession.user_id == user.id, AgentSession.archived_at.is_(None))
        .order_by(AgentSession.updated_at.desc())
    ).all()
    return [serialize_session(session) for session in sessions]


@router.get("/sessions/{session_id}", response_model=list[AgentMessageResponse])
def get_session_history(
    session_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    load_owned_session(db, user, session_id)
    messages = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id, AgentMessage.user_id == user.id)
        .order_by(AgentMessage.created_at.asc())
    ).all()
    return [serialize_message(message) for message in messages]


@router.delete("/sessions/{session_id}")
def archive_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = load_owned_session(db, user, session_id)
    session.archived_at = now_utc()
    session.updated_at = now_utc()
    db.commit()
    return {"ok": True}


def sse_event(event_type: str, payload: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


def recent_chat_messages(db: Session, session_id: UUID, user: User, limit: int = 12) -> list[dict]:
    rows = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id, AgentMessage.user_id == user.id)
        .order_by(AgentMessage.created_at.desc())
        .limit(limit)
    ).all()
    messages = []
    for row in reversed(rows):
        if row.role in {"user", "assistant"}:
            messages.append({"role": row.role, "content": row.content})
    return messages


def stream_agent_turn(
    db: Session,
    user: User,
    session: AgentSession,
    user_content: str,
):
    runner = LangGraphAgentRunner(
        AgentRuntime(db=db, user=user, session=session),
        recent_chat_messages(db, session.id, user),
    )

    for event in runner.iter_events(user_content):
        event_type = str(event.get("type") or "event")
        payload = {key: value for key, value in event.items() if key != "type"}
        yield sse_event(event_type, payload)

    assistant_message = AgentMessage(
        session_id=session.id,
        user_id=user.id,
        role="assistant",
        content=runner.record.final_answer,
        metadata_json={
            "sources": runner.record.sources,
            "rag_backend": runner.record.rag_backend,
            "artifacts": runner.record.artifacts,
            "suggestions": runner.record.suggestions,
            "events": runner.record.events,
            "agent_architecture": "langgraph_planner_executor_synthesizer",
            "generated_at": datetime.utcnow().isoformat(),
        },
    )
    db.add(assistant_message)
    session.updated_at = now_utc()
    db.commit()
    db.refresh(assistant_message)
    yield sse_event("message_done", {"message_id": str(assistant_message.id)})
    yield sse_event("done", {})


@router.post("/sessions/{session_id}/message")
def send_message(
    session_id: UUID,
    payload: AgentMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = load_owned_session(db, user, session_id)
    user_message = AgentMessage(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content=payload.content,
        metadata_json={},
    )
    db.add(user_message)
    session.updated_at = now_utc()
    db.commit()
    db.refresh(user_message)

    return StreamingResponse(
        stream_agent_turn(db, user, session, payload.content),
        media_type="text/event-stream",
    )
