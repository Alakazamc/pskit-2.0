from pydantic import BaseModel, Field


class CreateAgentSessionRequest(BaseModel):
    title: str | None = None


class AgentSessionResponse(BaseModel):
    id: str
    title: str | None
    created_at: str
    updated_at: str


class AgentMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class AgentMessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: str
    metadata: dict

class RagSource(BaseModel):
    source: str
    heading: str | None = None
    score: float

