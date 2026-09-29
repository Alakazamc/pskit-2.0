from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class CreateAgentSessionRequest(BaseModel):
    title: str | None = None


class AgentSessionResponse(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ADR 0012：科研归属由会话解析，客户端不再提供 research_run_id。
    content: str = Field(min_length=1, max_length=20000)
    turn_id: UUID = Field(default_factory=uuid4)
    approval_id: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class AgentMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    metadata: dict


class ActiveAgentTurnResponse(BaseModel):
    turn_id: str
    client_turn_id: str
    status: str
    user_content: str
    error_code: str | None
    created_at: str
    updated_at: str


class RagSource(BaseModel):
    source: str
    heading: str | None = None
    score: float
