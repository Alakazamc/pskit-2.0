import hashlib
import json

from app.contracts.capabilities import Af3JobRequest


class Af3IdempotencyConflict(Exception):
    """An AF3 request key was reused with a different payload."""


def af3_request_fingerprint(payload: Af3JobRequest) -> str:
    """Hash a canonical AF3 request for idempotent submission.

    Args:
        payload: Validated AF3 job request.

    Returns:
        SHA-256 hex digest of sorted JSON fields.
    """
    canonical = json.dumps(
        payload.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
