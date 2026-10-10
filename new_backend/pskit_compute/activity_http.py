"""Opt-in private authenticated activity listener, independent of model endpoints."""

import asyncio
import ipaddress
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from starlette.responses import JSONResponse


@dataclass(frozen=True)
class ActivitySettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 18188
    token: str = field(default="", repr=False)

    def __post_init__(self):
        try:
            if type(self.enabled) is not bool:
                raise ValueError
            if not self.enabled:
                return
            address = ipaddress.ip_address(self.host)
            if (not address.is_loopback and (address.is_unspecified or address.is_multicast
                    or address.is_link_local or address.is_reserved or not address.is_private)
                    or type(self.port) is not int or not 1 <= self.port <= 65535
                    or not isinstance(self.token, str) or not 32 <= len(self.token) <= 256
                    or any(not 33 <= ord(char) <= 126 for char in self.token)):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError("ACTIVITY_CONFIG_INVALID") from exc

    @classmethod
    def from_environment(cls, environ):
        flag = environ.get("PSKIT_RECEIVER_ACTIVITY_ENABLED", "false").lower()
        if flag not in {"", "false", "0", "true", "1"}:
            raise ValueError("ACTIVITY_CONFIG_INVALID")
        if flag not in {"true", "1"}:
            return cls()
        try:
            host = environ.get("PSKIT_RECEIVER_ACTIVITY_HOST", "127.0.0.1")
            port = int(environ.get("PSKIT_RECEIVER_ACTIVITY_PORT", "18188"))
            token = environ.get("PSKIT_RECEIVER_ACTIVITY_TOKEN", "")
            return cls(True, host, port, token)
        except (ValueError, TypeError) as exc:
            raise ValueError("ACTIVITY_CONFIG_INVALID") from exc


class ActivityApp:
    def __init__(self, observer, token):
        if not 32 <= len(token) <= 256 or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError("ACTIVITY_CONFIG_INVALID")
        self.observer, self.authorization = observer, ("Bearer " + token).encode("ascii")
        self._observation_lock = asyncio.Lock()

    async def __call__(self, scope, receive, send):
        headers = scope.get("headers", [])
        authorization = [value for key, value in headers if key == b"authorization"]
        valid = (scope["type"] == "http" and scope.get("method") == "GET"
                 and scope.get("path") == "/activity" and not scope.get("query_string")
                 and len(headers) <= 64 and sum(len(key) + len(value) for key, value in headers) <= 8192
                 and len(authorization) == 1 and secrets.compare_digest(authorization[0], self.authorization)
                 and not any(key == b"transfer-encoding" or key == b"content-length" and value != b"0"
                             for key, value in headers))
        if valid:
            try:
                async with asyncio.timeout(1):
                    for _ in range(8):
                        body = await receive()
                        if body.get("type") != "http.request" or body.get("body"):
                            valid = False
                            break
                        if not body.get("more_body"):
                            break
                    else:
                        valid = False
            except TimeoutError:
                valid = False
        if not valid:
            await JSONResponse({"detail": "Not found"}, status_code=404)(scope, receive, send)
            return
        if self._observation_lock.locked():
            await JSONResponse({"detail": "Observation unavailable"}, status_code=503)(scope, receive, send)
            return
        try:
            async with self._observation_lock:
                task = asyncio.create_task(asyncio.to_thread(self.observer.observe))
                try:
                    document = await asyncio.shield(task)
                except asyncio.CancelledError:
                    # A disconnected request cannot free admission while its
                    # read-only worker is still running in the shared executor.
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            continue
                    if not task.cancelled():
                        task.exception()
                    raise
                response = JSONResponse(document, headers={"Cache-Control": "no-store"})
        except Exception:  # noqa: BLE001 - No filesystem/schema details cross this private boundary.
            response = JSONResponse({"detail": "Observation unavailable"}, status_code=503)
        await response(scope, receive, send)


@asynccontextmanager
async def activity_listener(observer, settings):
    if not settings.enabled:
        yield
        return
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(ActivityApp(observer, settings.token), host=settings.host,
        port=settings.port, log_level="warning", access_log=False, lifespan="off",
        h11_max_incomplete_event_size=8192, timeout_keep_alive=2))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise ValueError("ACTIVITY_LISTENER_UNAVAILABLE")
                await asyncio.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
