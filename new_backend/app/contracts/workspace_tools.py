"""Internal request and event contracts for controlled workspace tools."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
RelativePath = Annotated[str, Field(min_length=1, max_length=1024)]


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: Identity
    session_id: Identity
    attempt_id: Identity


class FileReadRequest(WorkspaceRequest):
    path: RelativePath
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=65_536, ge=1, le=1_048_576)
    encoding: Literal["utf-8", "binary"] = "utf-8"


class FileWriteRequest(WorkspaceRequest):
    path: RelativePath
    content: str = Field(max_length=2_000_000)
    encoding: Literal["utf-8", "base64"] = "utf-8"
    expected_revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class FileEditRequest(WorkspaceRequest):
    path: RelativePath
    old_text: str = Field(min_length=1, max_length=1_000_000)
    new_text: str = Field(max_length=1_000_000)
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    replace_all: bool = False


class FileListRequest(WorkspaceRequest):
    path: RelativePath = "."
    max_depth: int = Field(default=2, ge=1, le=8)
    max_entries: int = Field(default=200, ge=1, le=1000)


class FileFindRequest(FileListRequest):
    pattern: str = Field(min_length=1, max_length=256)


class FileGrepRequest(FileFindRequest):
    max_matches: int = Field(default=100, ge=1, le=500)
    max_bytes: int = Field(default=1_048_576, ge=1, le=4_194_304)


class CommandStartRequest(WorkspaceRequest):
    argv: list[str] | None = Field(default=None, min_length=1, max_length=64)
    shell: str | None = Field(default=None, min_length=1, max_length=32_768)
    cwd: RelativePath | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    max_output_bytes: int = Field(default=1_048_576, ge=1, le=4_194_304)

    @model_validator(mode="after")
    def one_command_form(self) -> CommandStartRequest:
        if (self.argv is None) == (self.shell is None):
            raise ValueError("exactly one of argv or shell is required")
        return self


class PythonStartRequest(WorkspaceRequest):
    code: str = Field(min_length=1, max_length=100_000)
    argv: list[str] = Field(default_factory=list, max_length=32)
    timeout_seconds: float = Field(default=30, gt=0, le=600)
    max_output_bytes: int = Field(default=1_048_576, ge=1, le=4_194_304)


class CommandControlRequest(WorkspaceRequest):
    process_id: str = Field(min_length=1, max_length=256)


class CommandStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_id: str
    status: Literal["running"] = "running"


class CommandStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_id: str
    status: Literal["queued", "running", "cancelling", "completed", "failed", "cancelled", "unknown"]
    exit_code: int | None = None
    termination_confirmed: bool = False


class WorkspaceToolEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    type: Literal["started", "stdout", "stderr", "usage", "exit", "error", "artifact"]
    data: str = ""
    exit_code: int | None = None
    truncated: bool = False
    wall_ms: int | None = Field(default=None, ge=0)
    cpu_core_ms: int | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)


class FileReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    encoding: Literal["utf-8", "base64"]
    size: int = Field(ge=0)
    offset: int = Field(ge=0)
    eof: bool
    revision: str | None = None


class FileWriteResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    size: int = Field(ge=0)
    revision: str


class FileEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["file", "directory"]
    size: int = Field(ge=0)
    revision: str | None = None


class FileListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entries: list[FileEntryResponse]


class FileSearchMatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line: int | None = Field(default=None, ge=1)
    text: str | None = None


class FileSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matches: list[FileSearchMatchResponse]
