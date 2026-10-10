"""FileCatalog operations on the catalog facade shared state."""

import uuid
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.contracts.catalog import FileRef, FileUploadRequest
from app.db.postgres import PostgresStatements
from app.domain.catalog_common import (
    IMAGE_MIME_TYPES,
    MAX_EXTRACTED_CHARS,
    MAX_IMAGE_FILE_BYTES,
    MAX_PDF_FILE_BYTES,
    MAX_PDF_PAGES,
    MAX_TEXT_FILE_BYTES,
    TEXT_FILE_SUFFIXES,
    InvalidFileUpload,
    image_mime_type,
)


class FileCatalog:
    def files_for(self, user_id: str) -> list[FileRef]:
        """List uploaded file metadata owned by a user."""
        if self.db:
            rows = self.db.execute(
                "SELECT id,name,size FROM catalog_files WHERE user_id=? ORDER BY rowid", (user_id,)
            ).fetchall()
            return [FileRef(id=row[0], name=row[1], size=row[2]) for row in rows]
        return list(self._files.get(user_id, []))


    def stored_bytes_for(self, user_id: str) -> int:
        """Sum a user's stored upload bytes across files."""
        if self.db:
            uploaded = self.db.execute(
                "SELECT COALESCE(SUM(size),0) FROM catalog_files WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]
            has_artifacts = isinstance(self.db, PostgresStatements) or self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_artifact_blobs'"
            ).fetchone() is not None
            artifacts = self.db.execute(
                "SELECT COALESCE(SUM(size),0) FROM agent_artifact_blobs WHERE user_id=?",
                (user_id,),
            ).fetchone()[0] if has_artifacts else 0
            return uploaded + artifacts
        return sum(file.size for file in self._files.get(user_id, []))


    def single_file_limit_for(self, user_id: str) -> int | None:
        """Return a guest upload size limit or ``None`` for a member."""
        policy = getattr(self, "identity_policy", None)
        if policy is not None and policy.tier_for(user_id) == "guest":
            return getattr(self, "guest_file_limit_bytes", 2 * 1024 * 1024)
        return None


    def total_storage_limit_for(self, user_id: str) -> int | None:
        """Return a guest storage limit or ``None`` for a member."""
        lookup = getattr(self, "admin_storage_limit_for", None)
        admin_limit = lookup(user_id) if lookup else None
        policy = getattr(self, "identity_policy", None)
        if policy is not None and policy.tier_for(user_id) == "guest":
            guest_limit = getattr(self, "guest_storage_limit_bytes", 10 * 1024 * 1024)
            return min(guest_limit, admin_limit) if admin_limit is not None else guest_limit
        return admin_limit


    def add_file(
        self, user_id: str, payload: FileUploadRequest,
        max_total_bytes: int | None = None,
    ) -> FileRef:
        """Validate and store a legacy JSON text upload.

        Args:
            user_id: File owner.
            payload: UTF-8 text with a declared byte length.
            max_total_bytes: Optional per-user storage override.

        Returns:
            Stored file metadata.

        Raises:
            InvalidFileUpload: Type, size, declared length, or quota is invalid.
        """
        if Path(payload.name).suffix.lower() not in TEXT_FILE_SUFFIXES:
            raise InvalidFileUpload("UNSUPPORTED_FILE_TYPE", 415)
        actual_size = len(payload.content.encode("utf-8"))
        if payload.size > MAX_TEXT_FILE_BYTES or actual_size > MAX_TEXT_FILE_BYTES:
            raise InvalidFileUpload("FILE_TOO_LARGE", 413)
        if actual_size != payload.size:
            raise InvalidFileUpload("FILE_SIZE_MISMATCH", 422)
        return self._persist_file(user_id, payload.name, payload.size, payload.content, None,
                                  max_total_bytes)


    def add_uploaded_file(
        self, user_id: str, name: str, raw: bytes, max_total_bytes: int | None = None,
    ) -> FileRef:
        """Validate and store raw text or PDF bytes for an owned upload.

        Text must decode as UTF-8. PDFs are checked for page and extracted
        text limits before being stored with their original bytes.

        Args:
            user_id: File owner.
            name: Submitted file name; only its basename is stored.
            raw: Original uploaded bytes.
            max_total_bytes: Optional per-user storage override.

        Returns:
            Stored file metadata.

        Raises:
            InvalidFileUpload: Type, size, encoding, PDF, or quota is invalid.
        """
        single_limit = self.single_file_limit_for(user_id)
        if single_limit is not None and len(raw) > single_limit:
            raise InvalidFileUpload("FILE_TOO_LARGE", 413)
        suffix = Path(name).suffix.lower()
        if suffix == ".pdf":
            if len(raw) > MAX_PDF_FILE_BYTES:
                raise InvalidFileUpload("FILE_TOO_LARGE", 413)
            if not raw.startswith(b"%PDF-"):
                raise InvalidFileUpload("INVALID_PDF", 422)
            try:
                reader = PdfReader(BytesIO(raw), strict=False)
                if len(reader.pages) > MAX_PDF_PAGES:
                    raise InvalidFileUpload("PDF_TOO_MANY_PAGES", 413)
                content = "\n".join((page.extract_text() or "") for page in reader.pages)
            except (PdfReadError, OSError, ValueError) as exc:
                raise InvalidFileUpload("INVALID_PDF", 422) from exc
            content = content[:MAX_EXTRACTED_CHARS].strip()
            if not content:
                raise InvalidFileUpload("PDF_NO_EXTRACTABLE_TEXT", 422)
        elif suffix in IMAGE_MIME_TYPES:
            if len(raw) > MAX_IMAGE_FILE_BYTES:
                raise InvalidFileUpload("FILE_TOO_LARGE", 413)
            if image_mime_type(name, raw) is None:
                raise InvalidFileUpload("INVALID_IMAGE", 422)
            content = ""
        elif suffix in TEXT_FILE_SUFFIXES:
            if len(raw) > MAX_TEXT_FILE_BYTES:
                raise InvalidFileUpload("FILE_TOO_LARGE", 413)
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise InvalidFileUpload("INVALID_TEXT_ENCODING", 422) from exc
        else:
            raise InvalidFileUpload("UNSUPPORTED_FILE_TYPE", 415)
        # PostgreSQL text excludes NUL; the original bytes stay intact in bytea.
        content = content.replace("\x00", "\ufffd")
        return self._persist_file(user_id, Path(name).name, len(raw), content, raw,
                                  max_total_bytes)


    def add_parsed_pdf(
        self, user_id: str, name: str, raw: bytes, content: str,
        max_total_bytes: int | None = None,
    ) -> FileRef:
        """Store an externally parsed PDF with its original bytes.

        Args:
            user_id: File owner.
            name: PDF filename; only its basename is stored.
            raw: Original PDF bytes.
            content: Extracted text produced by the bounded parser.
            max_total_bytes: Optional per-user storage override.

        Returns:
            Stored file metadata.

        Raises:
            InvalidFileUpload: The PDF or a guest storage limit is invalid.
        """
        if Path(name).suffix.lower() != ".pdf":
            raise InvalidFileUpload("UNSUPPORTED_FILE_TYPE", 415)
        if len(raw) > MAX_PDF_FILE_BYTES:
            raise InvalidFileUpload("FILE_TOO_LARGE", 413)
        single_limit = self.single_file_limit_for(user_id)
        if single_limit is not None and len(raw) > single_limit:
            raise InvalidFileUpload("FILE_TOO_LARGE", 413)
        if not raw.startswith(b"%PDF-"):
            raise InvalidFileUpload("INVALID_PDF", 422)
        normalized = content[:MAX_EXTRACTED_CHARS].strip()
        if not normalized:
            raise InvalidFileUpload("PDF_NO_EXTRACTABLE_TEXT", 422)
        return self._persist_file(user_id, Path(name).name, len(raw), normalized, raw,
                                  max_total_bytes)


    def _persist_file(
        self, user_id: str, name: str, size: int, content: str, raw: bytes | None,
        max_total_bytes: int | None = None,
    ) -> FileRef:
        """Persist validated file data under the user's storage quota.

        SQLite mode checks aggregate size and writes the row under one
        immediate transaction; mock mode updates in-memory collections.

        Args:
            user_id: File owner.
            name: Stored filename.
            size: Original byte length.
            content: Text made available to Agent context.
            raw: Optional original bytes for download.
            max_total_bytes: Optional storage limit override.

        Returns:
            New file reference.

        Raises:
            InvalidFileUpload: A single-file or total-storage limit is exceeded.
        """
        single_limit = self.single_file_limit_for(user_id)
        if single_limit is not None and size > single_limit:
            raise InvalidFileUpload("FILE_TOO_LARGE", 413)
        def current_limit():
            configured = self.total_storage_limit_for(user_id)
            if max_total_bytes is None:
                return configured
            return min(configured, max_total_bytes) if configured is not None else max_total_bytes
        total_limit = current_limit()
        file = FileRef(id=f"file-{uuid.uuid4()}", name=name, size=size)
        if self.db:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self._require_account_active(user_id)
                total_limit = current_limit()
                used = self.stored_bytes_for(user_id)
                if total_limit is not None and used + size > total_limit:
                    raise InvalidFileUpload("STORAGE_QUOTA_EXCEEDED", 413)
                self.db.execute(
                    "INSERT INTO catalog_files "
                    "(id,user_id,name,size,content,raw_content) VALUES (?,?,?,?,?,?)",
                    (file.id, user_id, file.name, file.size, content, raw),
                )
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            return file
        if total_limit is not None and self.stored_bytes_for(user_id) + size > total_limit:
            raise InvalidFileUpload("STORAGE_QUOTA_EXCEEDED", 413)
        self._files.setdefault(user_id, []).append(file)
        self._file_contents.setdefault(user_id, {})[file.id] = content
        if raw is not None:
            self._file_bytes.setdefault(user_id, {})[file.id] = raw
        return file


    def file_bytes_for(self, user_id: str, file_id: str) -> bytes | None:
        """Read an owned file's original bytes or encoded text fallback."""
        if self.db:
            row = self.db.execute(
                "SELECT raw_content,content FROM catalog_files WHERE user_id=? AND id=?",
                (user_id, file_id),
            ).fetchone()
            return (row[0] if row[0] is not None else row[1].encode("utf-8")) if row else None
        raw = self._file_bytes.get(user_id, {}).get(file_id)
        if raw is not None:
            return raw
        content = self._file_contents.get(user_id, {}).get(file_id)
        return content.encode("utf-8") if content is not None else None


    def file_content_for(self, user_id: str, file_id: str) -> str | None:
        """Read text extracted from an owned file for Agent context."""
        if self.db:
            row = self.db.execute(
                "SELECT content FROM catalog_files WHERE user_id=? AND id=?", (user_id, file_id)
            ).fetchone()
            return row[0] if row else None
        return self._file_contents.get(user_id, {}).get(file_id)


    def delete_file(self, user_id: str, file_id: str) -> bool:
        """Delete an owned upload and its mock or persisted content.

        Returns:
            Whether the file existed for this user.
        """
        if self.db:
            with self.db:
                self._require_account_active(user_id)
                return bool(self.db.execute(
                    "DELETE FROM catalog_files WHERE id=? AND user_id=?", (file_id, user_id)
                ).rowcount)
        files = self._files.get(user_id, [])
        if not any(item.id == file_id for item in files):
            return False
        self._files[user_id] = [item for item in files if item.id != file_id]
        self._file_contents.get(user_id, {}).pop(file_id, None)
        self._file_bytes.get(user_id, {}).pop(file_id, None)
        return True
