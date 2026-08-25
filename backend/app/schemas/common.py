from pydantic import BaseModel


class StatusItem(BaseModel):
    name: str
    status: str
    detail: str | None = None


class HealthResponse(BaseModel):
    ok: bool
    service: str
    version: str
    checks: dict[str, str] | None = None
