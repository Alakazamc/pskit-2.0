from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    def __init__(self, attempts: int, window_seconds: int) -> None:
        self.attempts = max(1, attempts)
        self.window_seconds = max(1, window_seconds)
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._operations = 0

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        for key, events in list(self._events.items()):
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                self._events.pop(key, None)

    def retry_after(self, key: str) -> int | None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events.get(key)
            if events is None:
                return None
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                self._events.pop(key, None)
                return None
            if len(events) < self.attempts:
                return None
            return max(1, int(events[0] + self.window_seconds - now) + 1)

    def record_failure(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._events[key].append(now)
            self._operations += 1
            if self._operations % 128 == 0:
                self._prune_locked(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)
