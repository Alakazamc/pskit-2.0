"""Bounded PDF extraction in a separate Python process."""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path

from app.domain.catalog import InvalidFileUpload
from app.domain.mcp_capacity import PdfCapacityStore
from app.ports.providers import ProviderUnavailable

logger = logging.getLogger("pskit.files")

ERROR_STATUS = {
    "FILE_TOO_LARGE": 413,
    "PDF_TOO_MANY_PAGES": 413,
    "INVALID_PDF": 422,
    "PDF_NO_EXTRACTABLE_TEXT": 422,
}


def _run_pdf_worker(raw: bytes, timeout_seconds: float) -> str:
    """Extract PDF text in a bounded subprocess.

    Args:
        raw: Original PDF bytes sent to the parser's stdin.
        timeout_seconds: Wall-clock limit for the child process.

    Returns:
        Extracted UTF-8 text.

    Raises:
        InvalidFileUpload: The PDF is invalid, too large, empty, or timed out.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "app.workers.pdf_parser"],
            input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            cwd=Path(__file__).resolve().parents[2], timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise InvalidFileUpload("FILE_PROCESSING_TIMEOUT", 504) from exc
    if result.returncode:
        code = result.stdout.decode("ascii", errors="ignore").strip()
        if code in ERROR_STATUS:
            raise InvalidFileUpload(code, ERROR_STATUS[code])
        raise InvalidFileUpload("FILE_PROCESSING_FAILED", 500)
    return result.stdout.decode("utf-8")


class PdfProcessingPool:
    """Limit concurrent PDF subprocesses locally and across SQLite peers."""

    def __init__(
        self, *, max_concurrent: int, queue_timeout_seconds: float,
        parse_timeout_seconds: float, shared: PdfCapacityStore | None = None,
    ) -> None:
        """Configure local slots, optional shared leases, and parse deadlines.

        Args:
            max_concurrent: Number of local parser subprocess slots.
            queue_timeout_seconds: Maximum wait to enter a slot.
            parse_timeout_seconds: Child process wall-clock limit.
            shared: Optional cross-process SQLite capacity store.
        """
        self._slots = asyncio.Semaphore(max_concurrent)
        self._queue_timeout_seconds = queue_timeout_seconds
        self._parse_timeout_seconds = parse_timeout_seconds
        self._shared = shared
        self._lease_seconds = max(10, parse_timeout_seconds + 5)

    async def extract(self, raw: bytes) -> str:
        """Extract PDF text while holding local and optional shared capacity.

        Cancellation keeps the lease until the subprocess actually exits.

        Args:
            raw: Original PDF bytes.

        Returns:
            Extracted text.

        Raises:
            InvalidFileUpload: Capacity, parser, or timeout failure.
        """
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self._queue_timeout_seconds)
        except TimeoutError as exc:
            raise InvalidFileUpload("FILE_PROCESSING_BUSY", 429) from exc
        release_after_worker = False
        token: str | None = None
        try:
            if self._shared is not None:
                deadline = asyncio.get_running_loop().time() + self._queue_timeout_seconds
                while token is None:
                    try:
                        token = self._shared.claim(self._lease_seconds)
                    except ProviderUnavailable as exc:
                        raise InvalidFileUpload("FILE_PROCESSING_FAILED", 500) from exc
                    if token is not None:
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise InvalidFileUpload("FILE_PROCESSING_BUSY", 429)
                    await asyncio.sleep(min(0.02, remaining))
            work = asyncio.create_task(asyncio.to_thread(
                _run_pdf_worker, raw, self._parse_timeout_seconds,
            ))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                release_after_worker = True
                work.add_done_callback(lambda done: self._release_cancelled_work(done, token))
                raise
        finally:
            if not release_after_worker:
                self._release(token)

    def _release(self, token: str | None) -> None:
        """Release the shared lease, then always free the local slot."""
        try:
            if token is not None and self._shared is not None:
                self._shared.release(token)
        finally:
            self._slots.release()

    def _release_cancelled_work(self, work: asyncio.Task[str], token: str | None) -> None:
        """Release capacity after a cancelled request's parser finishes."""
        try:
            work.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("PDF parsing failed after the upload request was cancelled")
        try:
            self._release(token)
        except ProviderUnavailable:
            logger.exception("PDF parsing lease could not be released after cancellation")
