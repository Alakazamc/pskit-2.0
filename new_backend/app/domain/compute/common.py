"""Shared transaction and canonical payload helpers."""

import hashlib
import json
from datetime import UTC, datetime


def utcnow():
    return datetime.now(UTC)


def payload_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def admission_lock(connection):
    # Same lock as the existing AF3 and quota setters: neither path can overspend the other.
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('pskit-run-admission',0))")
