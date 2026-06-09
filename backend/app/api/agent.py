import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.agent.graph import run_agent_graph
from app.agent.llm import LlmUnavailable, OpenAICompatibleClient, default_system_prompt
from app.db.models import AgentMessage, AgentSession, User, now_utc
from app.db.session import get_db
from app.schemas.agent import AgentMessageRequest, AgentMessageResponse, AgentSessionResponse, CreateAgentSessionRequest
from app.tools.external import ToolExecutionError
from app.tools.runner import ToolContext, execute_tool


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


def tool_call_name_and_args(tool_call: dict) -> tuple[str, str]:
    function = tool_call.get("function") or {}
    return function.get("name") or "", function.get("arguments") or "{}"


def normalize_assistant_message(message: dict) -> dict:
    normalized = {
        "role": "assistant",
        "content": message.get("content") or "",
    }
    if message.get("tool_calls"):
        normalized["tool_calls"] = message["tool_calls"]
    return normalized


def run_agent_turn(
    db: Session,
    user: User,
    session: AgentSession,
    user_content: str,
    retrieved_knowledge: list[dict],
    source_payload: list[dict],
    rag_backend: str,
) -> tuple[str, list[dict], list[dict]]:
    events: list[dict] = [{"type": "knowledge_sources", "backend": rag_backend, "sources": source_payload}]
    artifacts: list[dict] = []
    llm = OpenAICompatibleClient()

    messages = [{"role": "system", "content": default_system_prompt(retrieved_knowledge)}]
    messages.extend(recent_chat_messages(db, session.id, user))

    try:
        assistant = llm.chat(messages, tools=True)
        messages.append(normalize_assistant_message(assistant))

        for _step in range(4):
            tool_calls = assistant.get("tool_calls") or []
            if not tool_calls:
                content = assistant.get("content") or assistant.get("reasoning_content") or ""
                if content:
                    return content, events, artifacts
                break

            for call in tool_calls:
                call_id = call.get("id") or f"call_{len(events)}"
                name, raw_args = tool_call_name_and_args(call)
                events.append(
                    {
                        "type": "tool_call_started",
                        "tool_call_id": call_id,
                        "name": name,
                        "args": raw_args,
                    }
                )
                try:
                    result = execute_tool(
                        name,
                        raw_args,
                        ToolContext(db=db, user=user, session_id=session.id, tool_call_id=call_id),
                    )
                    events.append(
                        {
                            "type": "tool_call_finished",
                            "tool_call_id": call_id,
                            "name": name,
                            "result": result,
                        }
                    )
                    if result.get("artifact_id"):
                        artifact = {
                            "artifact_id": result["artifact_id"],
                            "filename": result.get("filename"),
                            "download_url": result.get("download_url"),
                        }
                        artifacts.append(artifact)
                        events.append({"type": "artifact_created", "artifact": artifact})
                    if result.get("task_id"):
                        events.append(
                            {
                                "type": "task_created",
                                "task_id": result["task_id"],
                                "task_type": result.get("task_type"),
                                "status": result.get("status"),
                            }
                        )
                    tool_result = json.dumps(result, ensure_ascii=False)
                except Exception as exc:
                    error = {
                        "error_type": "tool_execution_error",
                        "tool": name,
                        "message": str(exc),
                    }
                    events.append({"type": "error", "error": error})
                    tool_result = json.dumps(error, ensure_ascii=False)

                messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_result})

            assistant = llm.chat(messages, tools=True)
            messages.append(normalize_assistant_message(assistant))

        content = assistant.get("content") or "Tool execution finished, but the model returned no final text."
        return content, events, artifacts
    except LlmUnavailable as exc:
        answer = (
            "PSKit 2.0 Agent backend is running, but the LLM is not available for this request. "
            f"Reason: {exc}. Retrieved knowledge sources are still attached."
        )
        events.append({"type": "error", "error": {"error_type": "llm_unavailable", "message": str(exc)}})
        return answer, events, artifacts


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

    graph_state = run_agent_graph({"latest_user_message": payload.content})
    rag_backend = graph_state.get("rag_backend", "keyword_fallback")
    source_payload = graph_state.get("retrieved_knowledge", [])
    answer, events, artifacts = run_agent_turn(
        db,
        user,
        session,
        payload.content,
        source_payload,
        [{"source": item["source"], "heading": item["heading"], "score": item["score"]} for item in source_payload],
        rag_backend,
    )
    assistant_message = AgentMessage(
        session_id=session.id,
        user_id=user.id,
        role="assistant",
        content=answer,
        metadata_json={
            "sources": [
                {"source": item["source"], "heading": item["heading"], "score": item["score"]}
                for item in source_payload
            ],
            "rag_backend": rag_backend,
            "artifacts": artifacts,
            "generated_at": datetime.utcnow().isoformat(),
        },
    )
    db.add(assistant_message)
    db.commit()
    db.refresh(assistant_message)

    def stream():
        for event in events:
            event_type = event.pop("type")
            yield sse_event(event_type, event)
        yield sse_event("message_delta", {"delta": answer})
        yield sse_event("message_done", {"message_id": str(assistant_message.id)})
        yield sse_event("done", {})

    return StreamingResponse(stream(), media_type="text/event-stream")
