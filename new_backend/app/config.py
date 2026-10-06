import json
import os
import re
from dataclasses import dataclass, field
from ipaddress import ip_network
from typing import Literal
from urllib.parse import urlparse


@dataclass(frozen=True)
class McpServerSettings:
    """Validated connection and allowlist for one remote MCP server."""
    id: str
    url: str
    allowed_tools: frozenset[str]
    bearer_token_env: str = ""


@dataclass(frozen=True)
class Settings:
    """Runtime settings for mock and live identity, Agent, and tool adapters."""
    mode: Literal["mock", "live"] = "mock"
    agent_runtime: Literal["mock", "pi"] = "mock"
    agent_db_path: str = "./data/agent.sqlite3"
    database_url: str = field(default_factory=lambda: os.getenv("RESEARCH_AGENT_DATABASE_URL", ""))
    database_schema: str = field(default_factory=lambda: os.getenv("RESEARCH_AGENT_DATABASE_SCHEMA", "pskit"))
    pdf_parse_timeout_seconds: float = 30.0
    pdf_max_concurrent_parses: int = 2
    pdf_queue_timeout_seconds: float = 5.0
    internal_api_url: str = "http://127.0.0.1:18080"
    mock_af3_seconds: float = 1.5
    af3_executor: Literal["auto", "mock", "callback", "disabled"] = "auto"
    mcp_executor: Literal["auto", "mock", "remote", "disabled"] = "auto"
    mcp_url: str = ""
    mcp_bearer_token: str = ""
    mcp_timeout_seconds: float = 30
    mcp_refresh_seconds: float = 30
    mcp_max_concurrent_calls: int = 8
    mcp_queue_timeout_seconds: float = 10
    mcp_allowed_tools_json: str = "[]"
    mcp_servers_json: str = "[]"
    compute_enabled: bool = False
    compute_services_json: str = "[]"
    compute_service_keys_json: str = "{}"
    compute_cpu_daily_limit_ms: int = 0
    compute_callback_key: str = ""
    af3_approval_threshold: int = 30
    af3_queue_timeout_seconds: int = 3600
    af3_execution_timeout_seconds: int = 21600
    af3_min_gpu_memory_mb: int = 0
    resume_retry_seconds: float = 1.0
    pi_executable: str = "pi"
    pi_provider: str = ""
    pi_model: str = ""
    pi_session_dir: str = "./data/pi-sessions"
    pi_execution: Literal["local", "sandbox"] = "local"
    sandbox_manager_url: str = ""
    sandbox_manager_token: str = ""
    pi_max_active_runs: int = 4
    pi_max_active_runs_per_user: int = 2
    supabase_url: str = ""
    supabase_public_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = field(default="", repr=False)
    auth_cookie_secure: bool = False
    frontend_url: str = "http://localhost:5174"
    public_api_url: str = "http://localhost:18080"
    new_api_base_url: str = ""
    new_api_model: str = ""
    new_api_user_tokens_json: str = "{}"
    model_gateway_base_url: str = ""
    model_gateway_model: str = ""
    model_gateway_api_key: str = ""
    model_gateway_kind: Literal["generic", "litellm"] = "generic"
    model_gateway_image_models_json: str = "[]"
    title_model: str = ""
    title_timeout_seconds: float = 15
    user_token_limits_json: str = "{}"
    admin_api_key: str = ""
    model_policy_mode: Literal["legacy", "managed"] = "legacy"
    admin_service_endpoints_json: str = "{}"
    admin_service_credentials_json: str = "{}"
    anonymous_enabled: bool | None = None
    anonymous_captcha_required: bool = False
    anonymous_rate_secret: str = ""
    anonymous_rate_limit_per_hour: int = 10
    auth_abuse_mode: Literal["off", "observe", "enforce"] = "off"
    auth_rate_limit_secret: str = field(default="", repr=False)
    auth_csrf_secret: str = field(default="", repr=False)
    auth_trusted_proxy_cidrs_json: str = "[]"
    auth_captcha_required: bool = False
    turnstile_secret_key: str = field(default="", repr=False)
    turnstile_hostnames_json: str = "[]"
    guest_monthly_token_limit: int = 20_000
    guest_daily_gpu_minute_limit: int = 0
    member_monthly_token_limit: int = 1_000_000
    member_daily_gpu_minute_limit: int = 60
    guest_max_active_runs: int = 1
    guest_mcp_allowed_tools_json: str = "[]"
    guest_file_limit_bytes: int = 2 * 1024 * 1024
    guest_storage_limit_bytes: int = 10 * 1024 * 1024

    @classmethod
    def from_env(cls) -> "Settings":
        """Read process environment values into immutable runtime settings.

        Returns:
            Settings with documented defaults for unset variables.

        Raises:
            ValueError: A numeric environment value cannot be parsed.
        """
        return cls(
            mode=os.getenv("RESEARCH_AGENT_MODE", "mock"),
            agent_runtime=os.getenv("RESEARCH_AGENT_RUNTIME", "mock"),
            agent_db_path=os.getenv("RESEARCH_AGENT_DB_PATH", "./data/agent.sqlite3"),
            database_url=os.getenv("RESEARCH_AGENT_DATABASE_URL", ""),
            database_schema=os.getenv("RESEARCH_AGENT_DATABASE_SCHEMA", "pskit"),
            pdf_parse_timeout_seconds=float(os.getenv("RESEARCH_AGENT_PDF_PARSE_TIMEOUT_SECONDS", "30")),
            pdf_max_concurrent_parses=int(os.getenv("RESEARCH_AGENT_PDF_MAX_CONCURRENT_PARSES", "2")),
            pdf_queue_timeout_seconds=float(os.getenv("RESEARCH_AGENT_PDF_QUEUE_TIMEOUT_SECONDS", "5")),
            internal_api_url=os.getenv("RESEARCH_AGENT_INTERNAL_API_URL", "http://127.0.0.1:18080"),
            mock_af3_seconds=float(os.getenv("RESEARCH_AGENT_MOCK_AF3_SECONDS", "1.5")),
            af3_executor=os.getenv("RESEARCH_AGENT_AF3_EXECUTOR", "auto"),
            mcp_executor=os.getenv("RESEARCH_AGENT_MCP_EXECUTOR", "auto"),
            mcp_url=os.getenv("RESEARCH_AGENT_MCP_URL", ""),
            mcp_bearer_token=os.getenv("RESEARCH_AGENT_MCP_BEARER_TOKEN", ""),
            mcp_timeout_seconds=float(os.getenv("RESEARCH_AGENT_MCP_TIMEOUT_SECONDS", "30")),
            mcp_refresh_seconds=float(os.getenv("RESEARCH_AGENT_MCP_REFRESH_SECONDS", "30")),
            mcp_max_concurrent_calls=int(os.getenv("RESEARCH_AGENT_MCP_MAX_CONCURRENT_CALLS", "8")),
            mcp_queue_timeout_seconds=float(os.getenv("RESEARCH_AGENT_MCP_QUEUE_TIMEOUT_SECONDS", "10")),
            mcp_allowed_tools_json=os.getenv("RESEARCH_AGENT_MCP_ALLOWED_TOOLS_JSON", "[]"),
            mcp_servers_json=os.getenv("RESEARCH_AGENT_MCP_SERVERS_JSON", "[]"),
            compute_enabled=os.getenv("RESEARCH_AGENT_COMPUTE_ENABLED", "false").lower() == "true",
            compute_services_json=os.getenv("RESEARCH_AGENT_COMPUTE_SERVICES_JSON", "[]"),
            compute_service_keys_json=os.getenv("RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON", "{}"),
            compute_cpu_daily_limit_ms=int(os.getenv("RESEARCH_AGENT_COMPUTE_CPU_DAILY_LIMIT_MS", "0")),
            compute_callback_key=os.getenv("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY", ""),
            af3_approval_threshold=int(os.getenv("RESEARCH_AGENT_AF3_APPROVAL_THRESHOLD", "30")),
            af3_queue_timeout_seconds=int(os.getenv("RESEARCH_AGENT_AF3_QUEUE_TIMEOUT_SECONDS", "3600")),
            af3_execution_timeout_seconds=int(
                os.getenv("RESEARCH_AGENT_AF3_EXECUTION_TIMEOUT_SECONDS", "21600")
            ),
            af3_min_gpu_memory_mb=int(os.getenv("RESEARCH_AGENT_AF3_MIN_GPU_MEMORY_MB", "0")),
            resume_retry_seconds=float(os.getenv("RESEARCH_AGENT_RESUME_RETRY_SECONDS", "1")),
            pi_executable=os.getenv("RESEARCH_AGENT_PI_EXECUTABLE", "pi"),
            pi_provider=os.getenv("RESEARCH_AGENT_PI_PROVIDER", ""),
            pi_model=os.getenv("RESEARCH_AGENT_PI_MODEL", ""),
            pi_session_dir=os.getenv("RESEARCH_AGENT_PI_SESSION_DIR", "./data/pi-sessions"),
            pi_execution=os.getenv("RESEARCH_AGENT_PI_EXECUTION", "local"),
            sandbox_manager_url=os.getenv("PSKIT_SANDBOX_MANAGER_URL", ""),
            sandbox_manager_token=os.getenv("PSKIT_SANDBOX_MANAGER_TOKEN", ""),
            pi_max_active_runs=int(os.getenv("RESEARCH_AGENT_PI_MAX_ACTIVE_RUNS", "4")),
            pi_max_active_runs_per_user=int(
                os.getenv("RESEARCH_AGENT_PI_MAX_ACTIVE_RUNS_PER_USER", "2")
            ),
            supabase_url=os.getenv("SUPABASE_URL", ""),
            supabase_public_url=os.getenv("SUPABASE_PUBLIC_URL", ""),
            supabase_publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY", ""),
            supabase_secret_key=os.getenv("SUPABASE_SECRET_KEY", ""),
            auth_cookie_secure=os.getenv("RESEARCH_AGENT_AUTH_COOKIE_SECURE", "false").lower() == "true",
            frontend_url=os.getenv("RESEARCH_AGENT_FRONTEND_URL", "http://localhost:5174"),
            public_api_url=os.getenv("RESEARCH_AGENT_PUBLIC_API_URL", "http://localhost:18080"),
            new_api_base_url=os.getenv("NEW_API_BASE_URL", ""),
            new_api_model=os.getenv("NEW_API_MODEL", ""),
            new_api_user_tokens_json=os.getenv("NEW_API_USER_TOKENS_JSON", "{}"),
            model_gateway_base_url=os.getenv("MODEL_GATEWAY_BASE_URL", ""),
            model_gateway_model=os.getenv("MODEL_GATEWAY_MODEL", ""),
            model_gateway_api_key=os.getenv("MODEL_GATEWAY_API_KEY", ""),
            model_gateway_kind=os.getenv("MODEL_GATEWAY_KIND", "generic"),
            model_gateway_image_models_json=os.getenv("MODEL_GATEWAY_IMAGE_MODELS_JSON", "[]"),
            title_model=os.getenv("RESEARCH_AGENT_TITLE_MODEL", ""),
            title_timeout_seconds=float(os.getenv("RESEARCH_AGENT_TITLE_TIMEOUT_SECONDS", "15")),
            user_token_limits_json=os.getenv("RESEARCH_AGENT_USER_TOKEN_LIMITS_JSON", "{}"),
            admin_api_key=os.getenv("RESEARCH_AGENT_ADMIN_API_KEY", ""),
            model_policy_mode=os.getenv("RESEARCH_AGENT_MODEL_POLICY_MODE", "legacy"),
            admin_service_endpoints_json=os.getenv("RESEARCH_AGENT_ADMIN_SERVICE_ENDPOINTS_JSON", "{}"),
            admin_service_credentials_json=os.getenv("RESEARCH_AGENT_ADMIN_SERVICE_CREDENTIALS_JSON", "{}"),
            anonymous_enabled=(None if "RESEARCH_AGENT_ANONYMOUS_ENABLED" not in os.environ
                               else os.getenv("RESEARCH_AGENT_ANONYMOUS_ENABLED", "").lower()
                               in {"1", "true", "yes"}),
            anonymous_captcha_required=os.getenv("RESEARCH_AGENT_ANONYMOUS_CAPTCHA_REQUIRED", "").lower()
            in {"1", "true", "yes"},
            anonymous_rate_secret=os.getenv("RESEARCH_AGENT_ANON_RATE_SECRET", ""),
            anonymous_rate_limit_per_hour=int(os.getenv("RESEARCH_AGENT_ANON_RATE_LIMIT_PER_HOUR", "10")),
            auth_abuse_mode=os.getenv("RESEARCH_AGENT_AUTH_ABUSE_MODE", "off"),
            auth_rate_limit_secret=os.getenv("RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET", ""),
            auth_csrf_secret=os.getenv("RESEARCH_AGENT_AUTH_CSRF_SECRET", ""),
            auth_trusted_proxy_cidrs_json=os.getenv(
                "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON", "[]"
            ),
            auth_captcha_required=os.getenv(
                "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED", "false"
            ).lower() in {"1", "true", "yes"},
            turnstile_secret_key=os.getenv("TURNSTILE_SECRET_KEY", ""),
            turnstile_hostnames_json=os.getenv("TURNSTILE_HOSTNAMES_JSON", "[]"),
            guest_monthly_token_limit=int(os.getenv("RESEARCH_AGENT_GUEST_MONTHLY_TOKEN_LIMIT", "20000")),
            guest_daily_gpu_minute_limit=int(os.getenv("RESEARCH_AGENT_GUEST_DAILY_GPU_MINUTES", "0")),
            member_monthly_token_limit=int(os.getenv("RESEARCH_AGENT_MEMBER_MONTHLY_TOKEN_LIMIT", "1000000")),
            member_daily_gpu_minute_limit=int(os.getenv("RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES", "60")),
            guest_max_active_runs=int(os.getenv("RESEARCH_AGENT_GUEST_MAX_ACTIVE_RUNS", "1")),
            guest_mcp_allowed_tools_json=os.getenv("RESEARCH_AGENT_GUEST_MCP_ALLOWED_TOOLS_JSON", "[]"),
            guest_file_limit_bytes=int(os.getenv("RESEARCH_AGENT_GUEST_FILE_LIMIT_BYTES", str(2 * 1024 * 1024))),
            guest_storage_limit_bytes=int(os.getenv("RESEARCH_AGENT_GUEST_STORAGE_LIMIT_BYTES", str(10 * 1024 * 1024))),
        )

    def effective_anonymous_enabled(self) -> bool:
        """Enable guests by default only in mock mode unless explicitly set."""
        return self.mode == "mock" if self.anonymous_enabled is None else self.anonymous_enabled

    def model_gateway_image_models(self) -> set[str]:
        """Read explicit vision-capable aliases when LiteLLM omits metadata."""
        ids = json.loads(self.model_gateway_image_models_json)
        if not isinstance(ids, list) or any(
            not isinstance(item, str) or not item or len(item) > 200 for item in ids
        ):
            raise ValueError("MODEL_GATEWAY_IMAGE_MODELS_JSON must be an array of model IDs")
        return set(ids)

    def require_live_config(self) -> None:
        """Reject missing or unsafe Supabase and Pi gateway configuration.

        Raises:
            ValueError: Live identity or Pi settings are incomplete, unsafe,
                or use the removed per-user New API token map.
        """
        if self.mode != "live":
            raise ValueError("Live configuration requested in mock mode")
        if self.model_gateway_kind not in {"generic", "litellm"}:
            raise ValueError("MODEL_GATEWAY_KIND must be generic or litellm")
        if not self.database_url:
            raise ValueError("RESEARCH_AGENT_DATABASE_URL is required in live mode")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.database_schema):
            raise ValueError("RESEARCH_AGENT_DATABASE_SCHEMA must be a SQL identifier")
        required = {
            "SUPABASE_URL": self.supabase_url,
            "SUPABASE_PUBLISHABLE_KEY": self.supabase_publishable_key,
        }
        if self.agent_runtime == "pi":
            required["MODEL_GATEWAY_BASE_URL"] = self.model_gateway_base_url or self.new_api_base_url
        for name, value in required.items():
            if not value:
                raise ValueError(f"{name} is required in live mode")
        self._validate_http_base_url(self.supabase_url, "SUPABASE_URL")
        self._validate_http_base_url(self.frontend_url, "RESEARCH_AGENT_FRONTEND_URL")
        if self.supabase_public_url:
            self._validate_http_base_url(self.supabase_public_url, "SUPABASE_PUBLIC_URL")
        if self.agent_runtime == "pi":
            gateway_name = "MODEL_GATEWAY_BASE_URL" if self.model_gateway_base_url else "NEW_API_BASE_URL"
            self._validate_http_base_url(
                self.model_gateway_base_url or self.new_api_base_url, gateway_name,
            )
        if self.agent_runtime == "pi" and not (self.model_gateway_model or self.new_api_model):
            raise ValueError("MODEL_GATEWAY_MODEL is required in live Pi mode")
        if self.agent_runtime == "pi" and self.pi_execution == "sandbox":
            if not self.sandbox_manager_url or not self.sandbox_manager_token:
                raise ValueError("Sandbox manager URL and token are required")
            self._validate_http_base_url(self.sandbox_manager_url, "PSKIT_SANDBOX_MANAGER_URL")
        mapping = json.loads(self.new_api_user_tokens_json)
        if mapping != {}:
            raise ValueError("NEW_API_USER_TOKENS_JSON is no longer supported; use MODEL_GATEWAY_API_KEY")
        if self.auth_abuse_mode not in {"off", "observe", "enforce"}:
            raise ValueError("RESEARCH_AGENT_AUTH_ABUSE_MODE must be off, observe, or enforce")
        if len(self.effective_auth_csrf_secret()) < 32:
            raise ValueError("RESEARCH_AGENT_AUTH_CSRF_SECRET must contain at least 32 characters")
        if self.auth_abuse_mode == "off":
            if self.auth_captcha_required:
                raise ValueError("Auth CAPTCHA cannot be required while auth abuse protection is off")
            return
        if len(self.auth_rate_limit_secret) < 32:
            raise ValueError("RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET must contain at least 32 characters")
        if not self.auth_trusted_proxy_cidrs():
            raise ValueError("RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON cannot be empty")
        if self.auth_captcha_required:
            if not self.turnstile_secret_key:
                raise ValueError("TURNSTILE_SECRET_KEY is required when auth CAPTCHA is enabled")
            if not self.turnstile_hostnames():
                raise ValueError("TURNSTILE_HOSTNAMES_JSON cannot be empty when auth CAPTCHA is enabled")

    def effective_auth_csrf_secret(self) -> str:
        """Use a dedicated CSRF key, with the existing HMAC key as a rollout fallback."""
        return self.auth_csrf_secret or self.auth_rate_limit_secret

    def auth_trusted_proxy_cidrs(self) -> tuple[str, ...]:
        """Parse and canonicalize the exact reverse-proxy networks allowed to assert client IPs."""
        try:
            values = json.loads(self.auth_trusted_proxy_cidrs_json)
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError
            networks = tuple(str(ip_network(value, strict=False)) for value in values)
            if tuple(values) != networks or len(set(networks)) != len(networks):
                raise ValueError
            return networks
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON must be an array of CIDRs"
            ) from exc

    def turnstile_hostnames(self) -> tuple[str, ...]:
        """Parse distinct lower-case hostnames accepted from Turnstile Siteverify."""
        try:
            values = json.loads(self.turnstile_hostnames_json)
        except json.JSONDecodeError as exc:
            raise ValueError("TURNSTILE_HOSTNAMES_JSON must be a hostname array") from exc
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError("TURNSTILE_HOSTNAMES_JSON must be a unique hostname array")
        normalized = [value.lower() for value in values]
        if len(set(normalized)) != len(normalized) or any(
            not self._valid_hostname(value) for value in values
        ):
            raise ValueError("TURNSTILE_HOSTNAMES_JSON must be a unique hostname array")
        return tuple(normalized)

    @staticmethod
    def _valid_hostname(value: str) -> bool:
        """Accept DNS hostnames without a scheme, port, wildcard, or path."""
        if not value or len(value) > 253 or value != value.strip() or value.endswith("."):
            return False
        labels = value.split(".")
        return all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        )

    @staticmethod
    def _validate_http_base_url(value: str, name: str) -> None:
        """Require an HTTP(S) base URL without embedded credentials or query.

        Args:
            value: URL to validate.
            name: Configuration key used in the error message.

        Raises:
            ValueError: The URL has an unsupported or unsafe shape.
        """
        try:
            parsed = urlparse(value)
            valid = (
                parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment and not parsed.params
                and parsed.port != 0
                and not any(character.isspace() for character in parsed.netloc)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{name} must be an HTTP(S) base URL without credentials or query parameters")

    def effective_af3_executor(self) -> str:
        """Resolve automatic AF3 mode to mock or disabled by identity mode."""
        if self.af3_executor == "auto":
            return "disabled" if self.mode == "live" else "mock"
        return self.af3_executor

    def effective_mcp_executor(self) -> str:
        """Resolve automatic MCP mode to mock or disabled by identity mode."""
        if self.mcp_executor == "auto":
            return "disabled" if self.mode == "live" else "mock"
        return self.mcp_executor

    def user_token_limits(self) -> dict[str, int]:
        """Parse explicit nonnegative Token limits keyed by user ID.

        Returns:
            User IDs mapped to monthly Token limits.

        Raises:
            ValueError: The configured JSON has an invalid shape or value.
        """
        mapping = json.loads(self.user_token_limits_json)
        if not isinstance(mapping, dict) or any(
            not isinstance(k, str) or type(v) is not int or v < 0
            for k, v in mapping.items()
        ):
            raise ValueError("RESEARCH_AGENT_USER_TOKEN_LIMITS_JSON must map user IDs to nonnegative integers")
        return mapping

    def mcp_allowed_tools(self) -> set[str]:
        """Parse the unique tool allowlist for a single remote MCP server.

        Returns:
            Allowed tool names.

        Raises:
            ValueError: The JSON value is not a list of unique names.
        """
        names = json.loads(self.mcp_allowed_tools_json)
        if not isinstance(names, list) or any(not isinstance(name, str) or not name
                                               for name in names) or len(set(names)) != len(names):
            raise ValueError("RESEARCH_AGENT_MCP_ALLOWED_TOOLS_JSON must be a unique list of names")
        return set(names)

    def guest_mcp_allowed_tools(self) -> set[str]:
        """Parse guest-safe MCP tool names and forbid AF3 access.

        Returns:
            Guest-allowed tool names.

        Raises:
            ValueError: JSON, name syntax, uniqueness, or AF3 policy is invalid.
        """
        try:
            names = json.loads(self.guest_mcp_allowed_tools_json)
        except json.JSONDecodeError as exc:
            raise ValueError("RESEARCH_AGENT_GUEST_MCP_ALLOWED_TOOLS_JSON must be JSON") from exc
        if (not isinstance(names, list)
                or any(not isinstance(name, str)
                       or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", name) is None
                       for name in names)
                or len(set(names)) != len(names)):
            raise ValueError("RESEARCH_AGENT_GUEST_MCP_ALLOWED_TOOLS_JSON must list unique tool names")
        if "submit_af3" in names:
            raise ValueError("AF3 cannot be enabled through the guest MCP allowlist")
        return set(names)

    def mcp_servers(self) -> list[McpServerSettings]:
        """Parse up to 16 uniquely named remote MCP server configurations.

        Returns:
            Validated server settings, including per-server tool allowlists.

        Raises:
            ValueError: JSON, URL, ID, tool names, or token variable is invalid.
        """
        try:
            raw = json.loads(self.mcp_servers_json)
        except json.JSONDecodeError as exc:
            raise ValueError("RESEARCH_AGENT_MCP_SERVERS_JSON must be valid JSON") from exc
        if not isinstance(raw, list) or len(raw) > 16:
            raise ValueError("RESEARCH_AGENT_MCP_SERVERS_JSON must be a list of at most 16 servers")
        servers: list[McpServerSettings] = []
        ids: set[str] = set()
        for entry in raw:
            if not isinstance(entry, dict) or set(entry) - {
                "id", "url", "allowed_tools", "bearer_token_env",
            }:
                raise ValueError("RESEARCH_AGENT_MCP_SERVERS_JSON has invalid server fields")
            server_id = entry.get("id")
            url = entry.get("url")
            names = entry.get("allowed_tools")
            token_env = entry.get("bearer_token_env", "")
            parsed = urlparse(url) if isinstance(url, str) else None
            if (not isinstance(server_id, str)
                    or re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", server_id) is None
                    or server_id in ids
                    or parsed is None or parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or not isinstance(names, list) or not names
                    or any(not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_]+", name) is None
                           for name in names)
                    or len(set(names)) != len(names)
                    or not isinstance(token_env, str)
                    or (token_env and re.fullmatch(r"[A-Z][A-Z0-9_]*", token_env) is None)):
                raise ValueError("RESEARCH_AGENT_MCP_SERVERS_JSON has invalid server configuration")
            ids.add(server_id)
            servers.append(McpServerSettings(
                id=server_id, url=url, allowed_tools=frozenset(names),
                bearer_token_env=token_env,
            ))
        return servers
