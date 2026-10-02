from typing import Literal

from pydantic import BaseModel, Field


class CatalogItem(BaseModel):
    id: str
    name: str
    description: str = ""
    version: int = 1


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
