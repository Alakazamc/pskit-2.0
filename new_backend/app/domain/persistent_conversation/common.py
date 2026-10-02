"""Shared values for the persistent conversation store modules."""

from datetime import UTC, datetime

from pydantic import TypeAdapter

from app.contracts.conversation import MessagePart


def _now() -> datetime:
    """Return the UTC clock used by persistent run and quota operations."""
    return datetime.now(UTC)


def current_time() -> datetime:
    """Resolve the public clock at call time so existing clock overrides still apply."""
    from . import _now as public_now

    return public_now()


MESSAGE_PARTS_ADAPTER = TypeAdapter(list[MessagePart])


class ComputeLeaseConflict(Exception):
    """A compute result used an expired or mismatched lease token."""
    pass


class GpuReconciliationConflict(ValueError):
    """A GPU charge could not be reconciled with the recorded job state."""
    pass
