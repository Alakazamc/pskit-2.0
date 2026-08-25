from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=8, max_length=256)
    bootstrap_token: str | None = Field(default=None, max_length=512)


class UserResponse(BaseModel):
    id: str
    username: str
    role: str
