"""Small single-process guard for public authentication endpoints.

The production deployment has one Web process.  This middleware deliberately
counts successful and failed requests alike, so a script cannot exhaust the
database by repeatedly creating sessions with valid credentials.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class AuthAbuseMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, login_limit: int = 30, register_limit: int = 3, window_seconds: int = 60):
        super().__init__(app)
        self.login_limit = max(1, login_limit)
        self.register_limit = max(1, register_limit)
        self.window_seconds = max(1, window_seconds)
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @staticmethod
    def _client_ip(request: Request) -> str:
        # Only trust the direct peer.  X-Forwarded-For is attacker-controlled
        # unless the deployment explicitly normalizes it at the trusted proxy.
        return request.client.host if request.client else "unknown"

    def _limited(self, kind: str, ip: str) -> int | None:
        limit = self.login_limit if kind == "login" else self.register_limit
        now = time.monotonic()
        key = (kind, ip)
        with self._lock:
            events = self._events[key]
            cutoff = now - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return max(1, int(events[0] + self.window_seconds - now) + 1)
            events.append(now)
            return None

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path in {
            "/api/auth/login",
            "/api/auth/register",
        }:
            kind = "login" if request.url.path.endswith("/login") else "register"
            if kind == "register":
                mode = os.getenv("REGISTRATION_MODE", "first_user").strip().lower()
                if mode == "disabled":
                    return JSONResponse(
                        {"detail": "Public registration is disabled; ask an administrator for an account"},
                        status_code=403,
                    )
            retry_after = self._limited(kind, self._client_ip(request))
            if retry_after is not None:
                return JSONResponse(
                    {"detail": "Too many authentication requests"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)
