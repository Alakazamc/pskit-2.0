from typing import Literal

from pydantic import BaseModel, Field


class CatalogItem(BaseModel):
    id: str
    name: str
    description: str = ""
    version: int = 1
    visibility: Literal["private", "review_pending", "public", "rejected", "disabled"] = "public"
    owned_by_me: bool = False
    source: Literal["builtin", "upstream", "user"] = "builtin"
    available: bool = True


class SkillFile(BaseModel):
    path: str
    size: int = Field(ge=0)
    content: str | None = None


class SkillDetail(CatalogItem):
    files: list[SkillFile] = Field(default_factory=list)
    license_name: str | None = None
    source_url: str | None = None
    source_commit: str | None = None
    review_reason: str | None = None


class SkillReviewRequest(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=5, max_length=500)


class FileUploadRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    content: str


class FileRef(BaseModel):
    id: str
    name: str
    size: int
    status: Literal["ready"] = "ready"


class ArtifactRef(BaseModel):
    id: str
    name: str
    kind: str
    available: bool = False
    size: int | None = None
    sha256: str | None = None


class ArtifactPreview(BaseModel):
    id: str
    name: str
    kind: str
    text: str
