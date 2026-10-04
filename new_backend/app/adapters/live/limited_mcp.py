import asyncio
from threading import Event

from app.contracts.capabilities import McpInvokeResult, McpTool
from app.domain.mcp_capacity import McpCapacityStore
from app.ports.providers import DiscoverableMcpProvider, ProviderUnavailable


class McpCapacityExceeded(Exception):
    """A tool call could not enter the MCP execution pool in time."""


class LimitedMcp:
    """Share a bounded MCP execution pool across public and internal calls."""

    def __init__(
        self,
        provider: DiscoverableMcpProvider,
        *,
        max_calls: int,
        queue_timeout_seconds: float,
        shared: McpCapacityStore | None = None,
        lease_seconds: float = 35,
    ) -> None:
        """Set local concurrency and optional shared lease limits for MCP calls."""
        self.provider = provider
        self.queue_timeout_seconds = queue_timeout_seconds
        self.shared = shared
        self.lease_seconds = lease_seconds
        self._slots = asyncio.BoundedSemaphore(max_calls)

    def tools(self) -> list[McpTool]:
        """Expose the wrapped provider's current discovered tools."""
        return self.provider.tools()

    async def discover(self) -> list[McpTool]:
        """Refresh tool definitions through the wrapped provider."""
        return await self.provider.discover()

    async def invoke(self, name: str, arguments: dict) -> McpInvokeResult | None:
        """Run a tool while holding local and optional shared capacity.

        Raises:
            McpCapacityExceeded: No slot became available before the deadline.
            ProviderUnavailable: A shared capacity lease was lost mid-call.
        """
        deadline = asyncio.get_running_loop().time() + self.queue_timeout_seconds
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=self.queue_timeout_seconds)
        except TimeoutError as exc:
            raise McpCapacityExceeded("MCP execution capacity exceeded") from exc
        token: str | None = None
        call: asyncio.Task | None = None
        heartbeat: asyncio.Task | None = None
        stop_heartbeat = Event()
        try:
            if self.shared is not None:
                while token is None:
                    token = self.shared.claim(self.lease_seconds)
                    if token is not None:
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise McpCapacityExceeded("MCP execution capacity exceeded")
                    await asyncio.sleep(min(0.025, remaining))
                heartbeat = asyncio.create_task(self._renew(token, stop_heartbeat))
            call = asyncio.create_task(self.provider.invoke(name, arguments))
            if heartbeat is None:
                return await call
            done, _pending = await asyncio.wait(
                {call, heartbeat}, return_when=asyncio.FIRST_COMPLETED
            )
            if heartbeat in done:
                raise ProviderUnavailable("MCP capacity lease lost")
            return await call
        finally:
            stop_heartbeat.set()
            if call is not None:
                call.cancel()
            await asyncio.gather(
                *(task for task in (call, heartbeat) if task is not None), return_exceptions=True
            )
            if token is not None and self.shared is not None:
                self.shared.release(token)
            self._slots.release()

    async def _renew(self, token: str, stop: Event) -> None:
        """Renew independently of event-loop latency and finish before releasing capacity."""
        assert self.shared is not None

        def maintain() -> None:
            while not stop.wait(min(5, self.lease_seconds / 3)):
                if not self.shared.renew(token, self.lease_seconds):
                    raise ProviderUnavailable("MCP capacity lease lost")

        await asyncio.to_thread(maintain)
