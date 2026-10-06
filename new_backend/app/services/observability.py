import json
import logging
import uuid
from collections import defaultdict
from time import perf_counter

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("pskit.requests")


class RequestMetrics:
    """Accumulate in-process request counts and summed durations by route."""

    def __init__(self) -> None:
        """Initialize an empty route metrics map."""
        self._values: dict[tuple[str, str, int], tuple[int, float]] = defaultdict(lambda: (0, 0.0))
        self._auth_security: dict[tuple[str, str, str, str], int] = defaultdict(int)

    def record(self, method: str, route: str, status: int, duration_ms: float) -> None:
        """Add one request's count and elapsed time to its route bucket."""
        key = (method, route, status)
        count, total = self._values[key]
        self._values[key] = (count + 1, total + duration_ms)

    def record_auth_security(
        self, component: str, action: str, outcome: str, rule: str = ""
    ) -> None:
        """Count a fixed auth outcome without recording email, IP, token or request data."""
        self._auth_security[(component, action, outcome, rule)] += 1

    def snapshot(self) -> dict[str, list[dict[str, str | int | float]]]:
        """Return sorted counters suitable for the protected metrics route."""
        return {
            "requests": [
                {
                    "method": method,
                    "route": route,
                    "status": status,
                    "count": count,
                    "duration_ms_sum": round(total, 3),
                }
                for (method, route, status), (count, total) in sorted(self._values.items())
            ],
            "auth_security": [
                {
                    "component": component,
                    "action": action,
                    "outcome": outcome,
                    "rule": rule,
                    "count": count,
                }
                for (component, action, outcome, rule), count in sorted(self._auth_security.items())
            ],
        }


class ObservabilityMiddleware:
    """Attach request IDs and record safe request metadata after responses."""

    def __init__(self, app: ASGIApp, metrics: RequestMetrics) -> None:
        """Wrap an ASGI app with a shared in-process metrics collector."""
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Track an HTTP request without logging query, body, or auth data."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = perf_counter()
        request_id = str(uuid.uuid4())
        status = 500

        async def send_with_request_id(message: dict) -> None:
            """Add the response request ID and capture the final status code."""
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            elapsed = (perf_counter() - started) * 1000
            self.metrics.record(scope["method"], route, status, elapsed)
            logger.info(
                json.dumps(
                    {
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": route,
                        "status": status,
                        "duration_ms": round(elapsed, 3),
                    }
                )
            )
