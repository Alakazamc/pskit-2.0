"""Private immutable avatar objects, separate from account metadata."""

from typing import Protocol


class AvatarStorage(Protocol):
    async def put(self, key: str, content: bytes) -> None:
        """Store a new normalized WebP object."""
        ...

    async def get(self, key: str) -> bytes | None:
        """Read a private object, returning None when absent."""
        ...

    async def delete(self, key: str) -> None:
        """Delete an obsolete object idempotently."""
        ...
