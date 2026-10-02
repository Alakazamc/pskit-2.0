from app.domain.identity_policy import IdentityPolicyStore


class LoginRequired(Exception):
    """Raised when a member-only capability is requested by a guest."""


class GuestCapabilityPolicy:
    """Apply verified account tier to AF3 and operator-approved MCP tools."""

    def __init__(self, identities: IdentityPolicyStore, guest_mcp_tools: set[str]) -> None:
        """Bind tier lookup and the explicit guest MCP allowlist.

        Args:
            identities: Store that resolves a user's verified account tier.
            guest_mcp_tools: Tool names permitted for guest accounts.
        """
        self.identities = identities
        self.guest_mcp_tools = frozenset(guest_mcp_tools)

    def require_member(self, user_id: str) -> None:
        """Require an authenticated member for a restricted capability.

        Args:
            user_id: Account requesting the capability.

        Raises:
            LoginRequired: The account is a guest.
        """
        if self.identities.tier_for(user_id) != "member":
            raise LoginRequired

    def mcp_allowed_for(self, user_id: str, tool_name: str) -> bool:
        """Check whether a member or allowlisted guest may call an MCP tool.

        Args:
            user_id: Account requesting the tool.
            tool_name: Registered MCP tool name.

        Returns:
            Whether the account tier permits the tool.
        """
        return (self.identities.tier_for(user_id) == "member"
                or tool_name in self.guest_mcp_tools)
