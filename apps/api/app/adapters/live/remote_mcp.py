import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from urllib.parse import urlparse

import httpx
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.contracts.capabilities import McpInvokeResult, McpTool
from app.ports.providers import ProviderUnavailable

MAX_RESULT_BYTES = 1024 * 1024


def _has_external_ref(value: object) -> bool:
    """Detect JSON Schema references outside the schema document."""
    if isinstance(value, list):
        return any(_has_external_ref(item) for item in value)
    if isinstance(value, dict):
        return any(
            (key in {"$ref", "$dynamicRef"}
             and (not isinstance(item, str) or not (item == "#" or item.startswith("#/"))))
            or _has_external_ref(item)
            for key, item in value.items()
        )
    return False


class RemoteMcp:
    """A single Streamable HTTP MCP server with startup discovery and bounded calls."""

    def __init__(
        self, url: str, *, allowed_tools: set[str], bearer_token: str = "", timeout_seconds: float = 30,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure an allowlisted Streamable HTTP MCP endpoint.

        Raises:
            ValueError: URL, timeout, or tool allowlist is invalid.
        """
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("RESEARCH_AGENT_MCP_URL must be an HTTP(S) URL")
        if timeout_seconds <= 0:
            raise ValueError("RESEARCH_AGENT_MCP_TIMEOUT_SECONDS must be positive")
        if not allowed_tools or any(not name or not isinstance(name, str)
                                    for name in allowed_tools):
            raise ValueError("RESEARCH_AGENT_MCP_ALLOWED_TOOLS_JSON must contain tool names")
        self.url = url
        self.allowed_tools = frozenset(allowed_tools)
        self.bearer_token = bearer_token
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._tools: list[McpTool] = []
        self._schemas: list[dict] = []

    def tools(self) -> list[McpTool]:
        """Return cached tool definitions from the last discovery."""
        return list(self._tools)

    @asynccontextmanager
    async def _session(self):
        """Open and initialize one bounded Streamable HTTP MCP session."""
        async with AsyncExitStack() as stack:
            client = self._http_client
            if client is None:
                headers = ({"Authorization": f"Bearer {self.bearer_token}"}
                           if self.bearer_token else {})
                client = await stack.enter_async_context(
                    httpx.AsyncClient(headers=headers, timeout=self.timeout_seconds)
                )
            streams = await stack.enter_async_context(
                streamable_http_client(self.url, http_client=client)
            )
            session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
            await session.initialize()
            yield session

    async def discover(self) -> list[McpTool]:
        """Fetch allowlisted tools and validate their input JSON Schemas.

        Raises:
            ProviderUnavailable: Discovery or schema validation failed.
        """
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self._session() as session:
                    response = await session.list_tools()
            schemas = []
            for definition in response.tools:
                if definition.name not in self.allowed_tools:
                    continue
                schema = {"id": definition.name, "input_schema": definition.inputSchema}
                output = getattr(definition, "outputSchema", None)
                if output is not None:
                    schema["output_schema"] = output
                for value in (definition.inputSchema, output):
                    if value is not None:
                        Draft202012Validator.check_schema(value)
                        if _has_external_ref(value):
                            raise ValueError("External JSON Schema references are not allowed")
                schemas.append(schema)
            if len(json.dumps(schemas, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
                raise ValueError("MCP discovered schemas exceed limit")
            tools = [McpTool(
                name=tool.name, description=tool.description or "",
                input_schema=tool.inputSchema,
            ) for tool in response.tools if tool.name in self.allowed_tools]
            for tool in tools:
                Draft202012Validator.check_schema(tool.input_schema)
                if _has_external_ref(tool.input_schema):
                    raise ValueError("External JSON Schema references are not allowed")
            if len({tool.name for tool in tools}) != len(tools):
                raise ValueError("Duplicate MCP tool names")
        except Exception as exc:
            raise ProviderUnavailable("MCP discovery failed") from exc
        self._tools = tools
        self._schemas = schemas
        return self.tools()

    async def discover_schemas(self) -> list[dict]:
        """Discover actual input/output schemas without widening the existing tools DTO."""
        await self.discover()
        return [dict(schema) for schema in self._schemas]

    async def invoke(self, name: str, arguments: dict) -> McpInvokeResult | None:
        """Call a discovered tool and bound the returned result to 1 MiB.

        Returns:
            Tool result, or ``None`` for a tool outside the discovered allowlist.
        """
        if name not in {tool.name for tool in self._tools}:
            return None
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self._session() as session:
                    response = await session.call_tool(
                        name, arguments, read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
                    )
        except Exception as exc:
            raise ProviderUnavailable("MCP tool invocation failed") from exc
        if response.isError:
            raise ProviderUnavailable("MCP tool failed")
        structured = response.structuredContent
        if isinstance(structured, dict):
            result = structured
        else:
            text_blocks = [item.text[:65536] for item in response.content
                           if item.type == "text"]
            result = {"content": text_blocks}
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES:
            raise ProviderUnavailable("MCP result exceeds limit")
        return McpInvokeResult(tool=name, result=result)
