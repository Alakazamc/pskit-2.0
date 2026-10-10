"""Shared catalog contracts, limits and validation helpers; no store state."""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.conversation import ContextRef

MAX_TEXT_FILE_BYTES = 1024 * 1024
MAX_PDF_FILE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_FILE_BYTES = 4 * 1024 * 1024
IMAGE_MIME_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}
MAX_PDF_PAGES = 100
MAX_EXTRACTED_CHARS = 200_000
TEXT_FILE_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".fasta", ".fa",
    ".pdb", ".cif", ".mmcif", ".fastq", ".fq", ".vcf", ".bed",
    ".gff", ".gff3", ".gtf", ".gb", ".gbk", ".sdf", ".mol", ".mol2",
}
BUILTIN_TOOL_IDS = {"search_pdb", "fetch_uniprot", "submit_af3"}


class SkillManifest(BaseModel):
    """Validated file-backed Skill metadata and declared tool IDs."""
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    version: int = Field(ge=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    tools: list[str]


class InvalidFileUpload(Exception):
    """Stable client error code and HTTP status for an invalid upload."""

    def __init__(self, code: str, status: int) -> None:
        """Store the public file error code and HTTP response status."""
        self.code = code
        self.status = status


class ContextNotFound(Exception):
    """A selected Skill, resource, or file is unavailable to the user."""


class SkillVersionConflict(Exception):
    """A registered Skill version conflicts with an existing definition."""


@dataclass(frozen=True)
class ResolvedContext:
    """Server-verified prompt, instructions, tools, and file references."""
    user_prompt: str
    system_instructions: str
    allowed_tools: tuple[str, ...]
    attachments: tuple[ContextRef, ...]
    image_ids: tuple[str, ...] = ()


def image_mime_type(name: str, raw: bytes) -> str | None:
    """Return the permitted MIME type only when bytes match the image suffix."""
    suffix = Path(name).suffix.lower()
    mime = IMAGE_MIME_TYPES.get(suffix)
    if mime is None:
        return None
    valid = (
        (suffix == ".png" and raw.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix in {".jpg", ".jpeg"} and raw.startswith(b"\xff\xd8\xff")
            and raw.endswith(b"\xff\xd9"))
        or (suffix == ".gif" and raw[:6] in {b"GIF87a", b"GIF89a"})
        or (suffix == ".webp" and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")
    )
    return mime if valid else None


