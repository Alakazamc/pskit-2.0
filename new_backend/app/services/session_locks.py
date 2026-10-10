"""Process-local exclusion with bounded Session lock ownership."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _Entry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    references: int = 0


class SessionLockRegistry:
    """Retain an entry while either its owner or an acquiring waiter exists.

    Registry operations happen on one event loop without an intervening await;
    the durable database lease remains the cross-process admission authority.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def references(self, session_id: str) -> int:
        entry = self._entries.get(session_id)
        return entry.references if entry else 0

    @asynccontextmanager
    async def hold(self, session_id: str):
        entry = self._entries.setdefault(session_id, _Entry())
        entry.references += 1
        acquired = False
        try:
            await entry.lock.acquire()
            acquired = True
            yield
        finally:
            if acquired:
                entry.lock.release()
            entry.references -= 1
            # Identity check also protects against an accidental external reset.
            if entry.references == 0 and self._entries.get(session_id) is entry:
                self._entries.pop(session_id)
