import asyncio
import re

from app.contracts.capabilities import McpInvokeResult, McpTool
from app.ports.providers import DiscoverableMcpProvider, ProviderUnavailable


class MultiRemoteMcp:
    """Expose several MCP servers through collision-free, server-scoped tool names."""

    def __init__(self, servers: dict[str, DiscoverableMcpProvider]) -> None:
        """Register remote MCP servers under stable namespace IDs."""
        self._servers = servers
        self._tools: list[McpTool] = []
        self._routes: dict[str, tuple[DiscoverableMcpProvider, str]] = {}
        self._server_availability: dict[str, bool] = {}

    def tools(self) -> list[McpTool]:
        """Return a copy of the currently discovered, namespaced tools."""
        return list(self._tools)

    def server_statuses(self) -> dict[str, bool]:
        """Return each server's last discovery availability."""
        return dict(self._server_availability)

    async def discover(self) -> list[McpTool]:
        """Discover servers concurrently and build collision-free tool routes.

        Raises:
            ProviderUnavailable: Every server failed or public names collided.
        """
        discovered = await asyncio.gather(
            *(server.discover() for server in self._servers.values()), return_exceptions=True,
        )
        tools: list[McpTool] = []
        routes: dict[str, tuple[DiscoverableMcpProvider, str]] = {}
        availability: dict[str, bool] = {}
        available = 0
        for (server_id, server), result in zip(self._servers.items(), discovered, strict=True):
            if isinstance(result, Exception):
                availability[server_id] = False
                continue
            availability[server_id] = True
            available += 1
            for tool in result:
                if re.fullmatch(r"[A-Za-z0-9_]+", tool.name) is None:
                    continue
                public_name = f"{server_id}__{tool.name}"
                if public_name in routes:
                    raise ProviderUnavailable("MCP tool name collision")
                tools.append(McpTool(
                    name=public_name, description=tool.description,
                    input_schema=tool.input_schema,
                ))
                routes[public_name] = (server, tool.name)
        self._tools = tools
        self._routes = routes
        self._server_availability = availability
        if available == 0:
            raise ProviderUnavailable("All MCP servers are unavailable")
        return self.tools()

    async def invoke(self, name: str, arguments: dict) -> McpInvokeResult | None:
        """Route a namespaced tool call to its originating MCP server."""
        route = self._routes.get(name)
        if route is None:
            return None
        server, original_name = route
        result = await server.invoke(original_name, arguments)
        if result is None:
            return None
        return McpInvokeResult(tool=name, status=result.status, result=result.result,
                               run_id=result.run_id)
