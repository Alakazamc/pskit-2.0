"""Validation for inert, browsable Agent Skills ZIP packages."""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from app.contracts.catalog import SkillFile

MAX_SKILL_ARCHIVE_BYTES = 5 * 1024 * 1024
MAX_SKILL_UNPACKED_BYTES = 10 * 1024 * 1024
MAX_SKILL_FILES = 100
MAX_SKILL_INSTRUCTIONS = 65_536
ALLOWED_SKILL_SUFFIXES = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".py", ".sh", ".csv", ".tsv",
}


class InvalidSkillPackage(Exception):
    """Stable validation failure for an untrusted Skill archive."""

    def __init__(self, code: str) -> None:
        self.code = code


@dataclass(frozen=True, slots=True)
class ValidatedSkillPackage:
    slug: str
    name: str
    description: str
    instructions: str
    files: tuple[SkillFile, ...]
    sha256: str


def _frontmatter(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        raise InvalidSkillPackage("SKILL_FRONTMATTER_REQUIRED")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise InvalidSkillPackage("SKILL_FRONTMATTER_INVALID") from exc
    values: dict[str, str] = {}
    index = 1
    while index < end:
        line = lines[index]
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):\s*(.*)$", line)
        if not match:
            index += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in {">", ">-", "|", "|-"}:
            continuation: list[str] = []
            index += 1
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                continuation.append(lines[index].strip())
                index += 1
            values[key] = " ".join(part for part in continuation if part)
            continue
        values[key] = value.strip("\"'")
        index += 1
    name = values.get("name", "").strip()
    description = values.get("description", "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", name) or not description:
        raise InvalidSkillPackage("SKILL_FRONTMATTER_INVALID")
    return name, description


def validate_skill_zip(raw: bytes) -> ValidatedSkillPackage:
    """Validate a text-only Agent Skills package without executing its contents."""
    if not raw or len(raw) > MAX_SKILL_ARCHIVE_BYTES:
        raise InvalidSkillPackage("SKILL_ARCHIVE_TOO_LARGE")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except (OSError, zipfile.BadZipFile) as exc:
        raise InvalidSkillPackage("SKILL_ARCHIVE_INVALID") from exc
    members = [member for member in archive.infolist() if not member.is_dir()]
    if not members or len(members) > MAX_SKILL_FILES:
        raise InvalidSkillPackage("SKILL_ARCHIVE_INVALID")
    roots = {PurePosixPath(member.filename).parts[0] for member in members}
    prefix = next(iter(roots)) if len(roots) == 1 and all(
        len(PurePosixPath(member.filename).parts) > 1 for member in members
    ) else None
    files: list[SkillFile] = []
    total = 0
    skill_markdown: str | None = None
    for member in members:
        path = PurePosixPath(member.filename)
        parts = path.parts[1:] if prefix else path.parts
        if (
            member.flag_bits & 0x1
            or not parts
            or path.is_absolute()
            or any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
            or len(parts) > 8
            or any(len(part) > 120 for part in parts)
            or stat.S_ISLNK(member.external_attr >> 16)
        ):
            raise InvalidSkillPackage("SKILL_ARCHIVE_UNSAFE_PATH")
        logical = "/".join(parts)
        if PurePosixPath(logical).suffix.lower() not in ALLOWED_SKILL_SUFFIXES:
            raise InvalidSkillPackage("SKILL_FILE_TYPE_UNSUPPORTED")
        total += member.file_size
        if total > MAX_SKILL_UNPACKED_BYTES or (
            member.compress_size > 0 and member.file_size > member.compress_size * 100
        ):
            raise InvalidSkillPackage("SKILL_ARCHIVE_TOO_LARGE")
        try:
            content = archive.read(member).decode("utf-8")
        except (UnicodeDecodeError, RuntimeError, zipfile.BadZipFile) as exc:
            raise InvalidSkillPackage("SKILL_FILE_ENCODING_INVALID") from exc
        if "\x00" in content:
            raise InvalidSkillPackage("SKILL_FILE_ENCODING_INVALID")
        files.append(SkillFile(path=logical, size=member.file_size, content=content))
        if logical == "SKILL.md":
            skill_markdown = content
    if skill_markdown is None or len(skill_markdown) > MAX_SKILL_INSTRUCTIONS:
        raise InvalidSkillPackage("SKILL_MD_REQUIRED")
    slug, description = _frontmatter(skill_markdown)
    return ValidatedSkillPackage(
        slug=slug,
        name=slug.replace("-", " ").title(),
        description=description,
        instructions=skill_markdown,
        files=tuple(sorted(files, key=lambda item: item.path)),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


__all__ = ["InvalidSkillPackage", "ValidatedSkillPackage", "validate_skill_zip"]
