"""Read one PDF from stdin and emit extracted UTF-8 text or a stable error code."""

import resource
import sys
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.domain.catalog import MAX_EXTRACTED_CHARS, MAX_PDF_FILE_BYTES, MAX_PDF_PAGES


def main() -> int:
    """Parse stdin under CPU and memory limits and write text or an error code.

    Returns:
        Zero on extracted text; two for a stable validation failure.
    """
    address_hard = resource.getrlimit(resource.RLIMIT_AS)[1]
    address_cap = min(1024 * 1024 * 1024, address_hard) if address_hard >= 0 else 1024 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (address_cap, address_hard))
    cpu_hard = resource.getrlimit(resource.RLIMIT_CPU)[1]
    cpu_cap = min(15, cpu_hard) if cpu_hard >= 0 else 15
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_cap, cpu_hard))
    raw = sys.stdin.buffer.read(MAX_PDF_FILE_BYTES + 1)
    if len(raw) > MAX_PDF_FILE_BYTES:
        sys.stdout.write("FILE_TOO_LARGE")
        return 2
    if not raw.startswith(b"%PDF-"):
        sys.stdout.write("INVALID_PDF")
        return 2
    try:
        reader = PdfReader(BytesIO(raw), strict=False)
        if len(reader.pages) > MAX_PDF_PAGES:
            sys.stdout.write("PDF_TOO_MANY_PAGES")
            return 2
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except (PdfReadError, OSError, ValueError, IndexError):
        sys.stdout.write("INVALID_PDF")
        return 2
    text = text[:MAX_EXTRACTED_CHARS].strip()
    if not text:
        sys.stdout.write("PDF_NO_EXTRACTABLE_TEXT")
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
