"""Private AF3 discovery and a single execution lane for old and new job APIs."""

import secrets
from types import SimpleNamespace

from starlette.responses import JSONResponse


class McpBearerAuth:
    def __init__(self, app, token):
        if len(token) < 32:
            raise ValueError("AF3_MCP_TOKEN_REQUIRED")
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            authorization = dict(scope.get("headers", [])).get(b"authorization", b"")
            if not secrets.compare_digest(authorization, ("Bearer " + self.token).encode()):
                await JSONResponse({"detail": "Not found"}, status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class Af3ReceiverLane:
    """Finish the currently owned job before either interface may claim another."""

    identity = SimpleNamespace(service_id="af3-mcp")

    def __init__(self, generic, compatibility, *, generic_cleanup, compatibility_cleanup):
        self.workers = [(generic, generic_cleanup), (compatibility, compatibility_cleanup)]
        self.turn = 0

    async def run_once(self):
        active = [item for item in self.workers if item[0].journal.recover()]
        if len(active) > 1:
            raise RuntimeError("AF3_MULTIPLE_UNACKNOWLEDGED_JOBS")
        candidates = active or (self.workers[self.turn:] + self.workers[:self.turn])
        for worker, cleanup in candidates:
            outcome = await worker.run_once()
            if outcome.status == "acknowledged":
                cleanup(outcome.job_id)
            if outcome.status != "idle":
                self.turn = (self.workers.index((worker, cleanup)) + 1) % 2
                return outcome
        return outcome
