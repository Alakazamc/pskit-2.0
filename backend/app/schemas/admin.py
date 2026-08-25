from pydantic import BaseModel


class AdminUserResponse(BaseModel):
    id: str
    username: str
    role: str
    disabled: bool
    active_sessions: int
    created_at: str


class UpdateAdminUserRequest(BaseModel):
    disabled: bool | None = None
    role: str | None = None


class AdminMetricsResponse(BaseModel):
    users: int
    active_sessions: int
    tasks_by_status: dict[str, int]
    artifacts: int
    active_agent_turns: int
