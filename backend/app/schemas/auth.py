from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip()


class AdminCreateUserRequest(AuthRequest):
    role: Literal["admin", "user"] = "user"


class UserResponse(BaseModel):
    id: str
    username: str
    role: str


class RegistrationStatusResponse(BaseModel):
    enabled: bool
    mode: str
    first_user: bool
