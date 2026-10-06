from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class UserIdentity(BaseModel):
    id: str
    email: str
    name: str
    is_anonymous: bool = False
    avatar_revision: str | None = None


class ProfileUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        """Keep display names nonempty, single-line and bounded."""
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Display name must contain visible text on one line")
        return value


class DailyUsage(BaseModel):
    date: date
    tokens: int = Field(ge=0)
    gpu_ms: int = Field(ge=0)


class UsageActivity(BaseModel):
    start_date: date
    end_date: date
    timezone: Literal["UTC"] = "UTC"
    days: list[DailyUsage]


class DemoLoginRequest(BaseModel):
    email: str = Field(min_length=3)


class DemoLoginResponse(BaseModel):
    access_token: str
    user: UserIdentity


class EmailLoginRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class SignupRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    captcha_token: str | None = None


class EmailRequest(BaseModel):
    email: str = Field(min_length=3)
    captcha_token: str | None = None


class EmailUpgradeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    captcha_token: str | None = None


class EmailUpgradeVerifyRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    token: str = Field(pattern=r"^[0-9]{6}$")


class EmailUpgradeStartResponse(BaseModel):
    status: Literal["check_email"] = "check_email"


class GoogleUpgradeStartResponse(BaseModel):
    url: str


class OtpVerifyRequest(BaseModel):
    email: str = Field(min_length=3)
    token: str = Field(min_length=1)
    type: Literal["signup", "recovery"]


class PasswordUpdateRequest(BaseModel):
    password: str = Field(min_length=8)


class AuthSessionResponse(BaseModel):
    access_token: str
    expires_in: int
    user: UserIdentity


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class SignupResponse(BaseModel):
    status: Literal["check_email", "signed_in"]
    session: AuthSessionResponse | None = None


class RecoveryResponse(BaseModel):
    status: Literal["email_sent"] = "email_sent"


class QuotaCounter(BaseModel):
    limit: int
    used: int
    reserved: int
    remaining: int
    unit: Literal["tokens"]
    period: Literal["day", "month"]
    resets_at: datetime


class GpuQuota(BaseModel):
    limit: int
    used: int
    reserved: int
    remaining: int
    unit: Literal["gpu_minutes"]
    period: Literal["day"]
    resets_at: datetime


class StorageQuota(BaseModel):
    limit: int | None
    used: int
    remaining: int | None
    unit: Literal["bytes"] = "bytes"


class UsageSnapshot(BaseModel):
    tokens: QuotaCounter
    gpu: GpuQuota
    storage: StorageQuota | None = None


class UsageEntry(BaseModel):
    id: str
    resource: Literal["tokens", "gpu_minutes"]
    kind: Literal["reservation", "adjustment", "charge", "model_attempt", "job"]
    amount: int
    status: str
    period: str
    run_id: str | None = None
    job_id: str | None = None
    created_at: datetime
