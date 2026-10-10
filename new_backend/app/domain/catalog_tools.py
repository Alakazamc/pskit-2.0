"""ToolCatalog operations on the catalog facade shared state."""



from app.contracts.capabilities import McpTool
from app.contracts.catalog import CatalogItem
from app.domain.catalog_common import (
    BUILTIN_TOOL_IDS,
)


class ToolCatalog:
    def refresh_mcp_tools(self, tools: list[McpTool], available_tools: set[str]) -> None:
        """Refresh visible Skills and MCP resources after tool discovery.

        Existing uploads and the SQLite connection remain attached.

        Args:
            tools: Newly discovered MCP tools.
            available_tools: Tool IDs executable in the current runtime.
        """
        self._known_tools = BUILTIN_TOOL_IDS | {tool.name for tool in tools}
        self._available_tools = set(available_tools)
        self._sync_registered_skills()
        self._rebuild_skills()
        if not self._overridden_resources:
            self._resources = [
                CatalogItem(id=tool.name, name=tool.name, description=tool.description)
                for tool in tools
            ]


    def allowed_tool_ids_for(self, user_id: str) -> set[str] | None:
        """Collect tools granted through the user's permitted Skills."""
        self._sync_registered_skills()
        grants = self._allowed_skill_ids(user_id)
        if grants is None:
            return None
        return {
            tool for skill_id, skill in self._skill_specs.items() if skill_id in grants
            for tool in skill["tools"]
        }


    def tool_allowed_for(self, user_id: str, tool_name: str) -> bool:
        """Check Skill grants and guest tier policy for a tool call."""
        allowed = self.allowed_tool_ids_for(user_id)
        if allowed is not None and tool_name not in allowed:
            return False
        policy = getattr(self, "guest_capabilities", None)
        return policy is None or policy.mcp_allowed_for(user_id, tool_name)


    def resources_for(self, user_id: str) -> list[CatalogItem]:
        """List MCP resources visible under user grants and tier policy."""
        allowed = self.allowed_tool_ids_for(user_id)
        return [resource for resource in self._resources
                if (allowed is None or resource.id in allowed)
                and self.tool_allowed_for(user_id, resource.id)]


    def resources(self) -> list[CatalogItem]:
        """Return the unfiltered resource catalog for internal consumers."""
        return list(self._resources)
