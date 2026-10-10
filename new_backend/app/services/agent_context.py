import hashlib
import hmac
import json
import secrets

from app.contracts.conversation import (
    ToolResultPart,
)
from app.domain.internal_auth import WorkspaceToolClaims
from app.ports.workspace_sandbox import WorkspaceCapabilities


class AgentContextMixin:
    """Internal AgentService responsibility; the facade preserves its API."""

    def _environment(
        self,
        user_id: str,
        run_id: str,
        *,
        session_id: str | None = None,
        attempt_id: str | None = None,
        workspace_capabilities: WorkspaceCapabilities | None = None,
        context: dict | None = None,
    ) -> dict[str, str]:
        """Build Pi extension variables with tier-filtered tools and scoped auth.

        Args:
            user_id: Run owner whose capability policy applies.
            run_id: Run used to scope internal tool and model proxy tokens.

        Returns:
            Server-only environment variables for the Pi subprocess.
        """
        if context is None:
            context = self.store.run_context(run_id)
        allowed = set(context.get("allowed_tools", []))
        capabilities = getattr(self, "guest_capabilities", None)
        if capabilities is not None:
            allowed = {name for name in allowed if capabilities.mcp_allowed_for(user_id, name)}
        environment = {
            "PSKIT_USER_ID": user_id,
            "PSKIT_RUN_ID": run_id,
            "PSKIT_AGENT_TOOL_TOKEN": self.tool_token(run_id),
            "PSKIT_INTERNAL_API_URL": self.internal_api_url,
            "PSKIT_AF3_ENABLED": "1"
            if self.af3_executor != "disabled"
            and (capabilities is None or capabilities.identities.tier_for(user_id) == "member")
            else "0",
            "PSKIT_MCP_TOOLS_JSON": json.dumps(
                [tool.model_dump() for tool in self.mcp_tools if tool.name in allowed]
            ),
        }
        if session_id and attempt_id and workspace_capabilities is not None:
            environment.update(
                {
                    "PSKIT_SESSION_ID": session_id,
                    "PSKIT_ATTEMPT_ID": attempt_id,
                    "PSKIT_WORKSPACE_TOOL_TOKEN": self.workspace_tool_token(
                        user_id=user_id,
                        run_id=run_id,
                        session_id=session_id,
                        attempt_id=attempt_id,
                    ),
                    "PSKIT_WORKSPACE_CAPABILITIES_JSON": json.dumps(
                        {
                            "file_access": workspace_capabilities.file_access,
                            "command_execution": workspace_capabilities.command_execution,
                            "command_events": workspace_capabilities.command_events,
                            "cancellation": workspace_capabilities.cancellation,
                            "runtime": workspace_capabilities.runtime,
                            "session_mount_namespace": (
                                workspace_capabilities.session_mount_namespace
                            ),
                        },
                        separators=(",", ":"),
                    ),
                }
            )
        compute = getattr(self, "compute_jobs", None)
        member = capabilities is None or capabilities.identities.tier_for(user_id) == "member"
        environment["PSKIT_COMPUTE_CAPABILITIES_JSON"] = json.dumps(
            [capability.model_dump(mode="json") for capability in compute.catalog.for_user(user_id)]
            if compute is not None and member
            else []
        )
        projected_tools = getattr(self, "agent_compute_tools", None)
        environment["PSKIT_COMPUTE_TOOLS_JSON"] = json.dumps(
            projected_tools.for_user(user_id) if projected_tools is not None and member else [],
            separators=(",", ":"),
        )
        model_id = context.get("model_id")
        if model_id:
            environment["PSKIT_MODEL_ID"] = model_id
        profile = context.get("model_runtime_profile")
        if profile is not None:
            environment["PSKIT_MODEL_RUNTIME_PROFILE"] = json.dumps(profile, separators=(",", ":"))
        if context.get("model_supports_images") is True:
            environment["PSKIT_MODEL_SUPPORTS_IMAGES"] = "1"
        reasoning_effort = context.get("reasoning_effort")
        if reasoning_effort:
            environment["PSKIT_REASONING_EFFORT"] = reasoning_effort
        if self.model_gateway_api_key is not None:
            environment["MODEL_GATEWAY_API_KEY"] = (
                f"{run_id}.{self.tool_token(run_id)}"
                if self.model_gateway_proxy_enabled
                else self.model_gateway_api_key
            )
        return environment

    def _prepare_turn_context(self, user_id: str, session_id: str, run_id: str):
        """Load one leased turn's immutable inputs in one complete worker call."""
        if not self.store.owns_lease(run_id, self._instance_id) or not self.store.start_pi_turn(
            run_id, self._instance_id
        ):
            return None
        context = self.store.run_context(run_id)
        return (
            context,
            self.store.session_file_for(user_id, session_id),
            self._environment(user_id, run_id, context=context),
        )

    def tool_token(self, run_id: str) -> str:
        """Derive a Run-scoped HMAC token for Python internal tool routes."""
        return hmac.new(self._tool_secret, run_id.encode(), hashlib.sha256).hexdigest()

    def verify_tool_token(self, run_id: str, token: str) -> bool:
        """Compare an internal tool token without leaking timing differences."""
        return secrets.compare_digest(self.tool_token(run_id), token)

    def workspace_tool_token(
        self,
        *,
        user_id: str,
        run_id: str,
        session_id: str,
        attempt_id: str,
        ttl_seconds: int = 900,
    ) -> str:
        """Issue a short-lived token bound to one active workspace attempt."""
        return self._workspace_tool_tokens.issue(
            user_id=user_id,
            run_id=run_id,
            session_id=session_id,
            attempt_id=attempt_id,
            ttl_seconds=ttl_seconds,
        )

    def verify_workspace_tool_token(self, token: str) -> WorkspaceToolClaims | None:
        """Verify a workspace token and return its immutable scope."""
        return self._workspace_tool_tokens.verify(token)

    def _mcp_tool_result_parts(self, run_id: str) -> list[ToolResultPart]:
        """Project durable completed MCP calls into typed answer parts."""
        if self.mcp_tool_calls is None:
            return []
        return [
            ToolResultPart(tool_call_id=call_id, tool=name, result=record.result)
            for call_id, name, record in self.mcp_tool_calls.completed_for_run(run_id)
        ]
