import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from shutil import which
from time import monotonic

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.disabled import DisabledAf3, DisabledMcp
from app.adapters.live.limited_mcp import LimitedMcp
from app.adapters.live.multi_remote_mcp import MultiRemoteMcp
from app.adapters.live.pi_rpc import PiRpcRunner
from app.adapters.live.remote_mcp import RemoteMcp
from app.adapters.live.sandbox_pi import SandboxPiRunner
from app.adapters.live.supabase_auth import SupabaseIdentityAdapter
from app.adapters.mock.af3 import MockAf3
from app.adapters.mock.auth import MockIdentityProvider
from app.adapters.mock.mcp import MockMcp
from app.adapters.mock.persistent_af3 import PersistentMockAf3
from app.api import (
    admin,
    auth,
    capabilities,
    catalog,
    guest_auth,
    health,
    internal,
    metrics,
    runs,
    usage,
    workspace,
)
from app.config import Settings
from app.db.postgres import PostgresDatabase
from app.domain.catalog import CatalogStore
from app.domain.conversation import ConversationStore
from app.domain.guest_capabilities import GuestCapabilityPolicy, LoginRequired
from app.domain.guest_rate_limit import GuestRateLimiter
from app.domain.identity_policy import GuestAccountDeleting, IdentityPolicyStore
from app.domain.internal_auth import load_or_create_internal_tool_secret
from app.domain.mcp_capacity import McpCapacityStore, PdfCapacityStore
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.quota import QuotaLedger
from app.domain.store import DemoStore
from app.domain.tool_runs import ToolRunStore
from app.ports.providers import ProviderUnavailable
from app.services.agent import AgentService
from app.services.observability import ObservabilityMiddleware, RequestMetrics
from app.services.pdf_processing import PdfProcessingPool


def create_app(settings: Settings | None = None, *, pi_runner=None, mcp_provider=None) -> FastAPI:
    """Build the API with validated mock or live adapters.

    Args:
        settings: Explicit runtime settings; environment settings when absent.
        pi_runner: Optional injected Pi RPC runner for integration tests.
        mcp_provider: Optional injected MCP provider.

    Returns:
        Configured FastAPI app with stateful stores and routes.

    Raises:
        ValueError: A selected runtime lacks required or safe configuration.
    """
    settings = settings or Settings.from_env()
    if settings.mode not in {"mock", "live"}:
        raise ValueError("RESEARCH_AGENT_MODE must be mock or live")
    af3_executor = settings.effective_af3_executor()
    mcp_executor = settings.effective_mcp_executor()
    if af3_executor not in {"mock", "callback", "disabled"}:
        raise ValueError("RESEARCH_AGENT_AF3_EXECUTOR must be auto, mock, callback, or disabled")
    if mcp_executor not in {"mock", "remote", "disabled"}:
        raise ValueError("RESEARCH_AGENT_MCP_EXECUTOR must be auto, mock, remote, or disabled")
    mcp_servers = settings.mcp_servers() if mcp_executor == "remote" else []
    if mcp_executor == "remote" and mcp_servers and settings.mcp_url:
        raise ValueError("Use RESEARCH_AGENT_MCP_SERVERS_JSON or RESEARCH_AGENT_MCP_URL, not both")
    if mcp_executor == "remote" and mcp_provider is None:
        for server in mcp_servers:
            if server.bearer_token_env and not os.environ.get(server.bearer_token_env):
                raise ValueError(f"Missing MCP credential environment variable: {server.bearer_token_env}")
    if mcp_executor == "remote" and not settings.mcp_url and not mcp_servers and mcp_provider is None:
        raise ValueError("RESEARCH_AGENT_MCP_URL is required in remote mode")
    if mcp_executor == "remote" and not mcp_servers and mcp_provider is None and not settings.mcp_allowed_tools():
        raise ValueError("RESEARCH_AGENT_MCP_ALLOWED_TOOLS_JSON is required in remote mode")
    if mcp_executor == "remote" and settings.mcp_refresh_seconds <= 0:
        raise ValueError("RESEARCH_AGENT_MCP_REFRESH_SECONDS must be positive")
    if settings.mcp_max_concurrent_calls < 1:
        raise ValueError("RESEARCH_AGENT_MCP_MAX_CONCURRENT_CALLS must be positive")
    if settings.mcp_queue_timeout_seconds <= 0:
        raise ValueError("RESEARCH_AGENT_MCP_QUEUE_TIMEOUT_SECONDS must be positive")
    if settings.pdf_parse_timeout_seconds <= 0:
        raise ValueError("RESEARCH_AGENT_PDF_PARSE_TIMEOUT_SECONDS must be positive")
    if settings.pdf_max_concurrent_parses < 1:
        raise ValueError("RESEARCH_AGENT_PDF_MAX_CONCURRENT_PARSES must be positive")
    if settings.pdf_queue_timeout_seconds <= 0:
        raise ValueError("RESEARCH_AGENT_PDF_QUEUE_TIMEOUT_SECONDS must be positive")
    if settings.af3_approval_threshold < 1:
        raise ValueError("RESEARCH_AGENT_AF3_APPROVAL_THRESHOLD must be positive")
    if settings.af3_queue_timeout_seconds < 1:
        raise ValueError("RESEARCH_AGENT_AF3_QUEUE_TIMEOUT_SECONDS must be positive")
    if settings.af3_execution_timeout_seconds < 1:
        raise ValueError("RESEARCH_AGENT_AF3_EXECUTION_TIMEOUT_SECONDS must be positive")
    if not 0 <= settings.af3_min_gpu_memory_mb <= 1_048_576:
        raise ValueError("RESEARCH_AGENT_AF3_MIN_GPU_MEMORY_MB must be between 0 and 1048576")
    if (settings.pi_max_active_runs < 1 or settings.pi_max_active_runs_per_user < 1
            or settings.pi_max_active_runs_per_user > settings.pi_max_active_runs):
        raise ValueError("Pi concurrency limits must be positive and per-user <= global")
    if min(
        settings.guest_monthly_token_limit,
        settings.guest_daily_gpu_minute_limit,
        settings.member_monthly_token_limit,
        settings.member_daily_gpu_minute_limit,
    ) < 0:
        raise ValueError("Account Token and GPU limits must be nonnegative")
    if settings.guest_max_active_runs < 1:
        raise ValueError("RESEARCH_AGENT_GUEST_MAX_ACTIVE_RUNS must be positive")
    if settings.guest_file_limit_bytes < 1 or settings.guest_storage_limit_bytes < 0:
        raise ValueError("Guest file limit must be positive and storage limit nonnegative")
    if af3_executor == "callback" and settings.agent_runtime != "pi":
        raise ValueError("Callback AF3 execution requires the persistent Pi runtime")
    if af3_executor == "callback" and not settings.compute_callback_key:
        raise ValueError("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY is required in callback mode")
    if af3_executor == "callback" and settings.mode == "live" and settings.af3_min_gpu_memory_mb == 0:
        raise ValueError("RESEARCH_AGENT_AF3_MIN_GPU_MEMORY_MB is required for live AF3 callbacks")
    if settings.mode == "live":
        settings.require_live_config()
    if settings.anonymous_rate_limit_per_hour < 1:
        raise ValueError("RESEARCH_AGENT_ANON_RATE_LIMIT_PER_HOUR must be positive")
    if settings.mode == "live" and settings.effective_anonymous_enabled():
        if len(settings.anonymous_rate_secret) < 16:
            raise ValueError("RESEARCH_AGENT_ANON_RATE_SECRET must have at least 16 characters")
        if not settings.anonymous_captcha_required:
            raise ValueError("RESEARCH_AGENT_ANONYMOUS_CAPTCHA_REQUIRED must be enabled in live mode")
    if settings.agent_runtime == "pi" and pi_runner is None:
        if settings.pi_execution == "sandbox":
            if not settings.sandbox_manager_url or not settings.sandbox_manager_token:
                raise ValueError("Sandbox manager URL and token are required")
            pi_runner = SandboxPiRunner(
                manager_url=settings.sandbox_manager_url,
                manager_token=settings.sandbox_manager_token,
                model=settings.model_gateway_model or settings.new_api_model or settings.pi_model,
            )
        else:
            local_pi = Path(__file__).resolve().parents[1] / "pi" / "node_modules" / ".bin" / "pi"
            executable = str(local_pi) if settings.pi_executable == "pi" and local_pi.is_file() else settings.pi_executable
            if which(executable) is None and not Path(executable).is_file():
                raise ValueError("Pi executable not found; set RESEARCH_AGENT_PI_EXECUTABLE")
            pi_runner = PiRpcRunner(
                executable=executable,
                session_dir=settings.pi_session_dir,
                extension=str(Path(__file__).resolve().parents[1] / "pi" / "extension.js"),
                provider=settings.pi_provider or None,
                model=settings.pi_model or None,
                new_api_base_url=None,
                new_api_model=settings.new_api_model or None,
                model_gateway_base_url=(f"{settings.internal_api_url.rstrip('/')}/internal/model"
                                        if settings.mode == "live" else None),
                model_gateway_model=settings.model_gateway_model or None,
                system_prompt=(Path(__file__).resolve().parents[1] / "pi" / "system-prompt.md").read_text(),
            )
    database = (PostgresDatabase(settings.database_url, schema=settings.database_schema)
                if settings.mode == "live" else None)
    storage = database if database is not None else settings.agent_db_path
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Start discovery and background schedulers, then release them on exit."""
        service = application.state.agent_service
        mock_scheduler = None
        mcp_refresh_task = None
        if mcp_executor == "remote":
            async def refresh_mcp() -> None:
                """Discover allowed remote tools and refresh the visible catalog."""
                try:
                    tools = await application.state.mcp.discover()
                    application.state.mcp_unavailable = False
                except ProviderUnavailable:
                    tools = []
                    application.state.mcp_unavailable = True
                application.state.catalog.refresh_mcp_tools(
                    tools,
                    {tool.name for tool in tools}
                    | ({"submit_af3"} if af3_executor != "disabled" else set()),
                )
                if service:
                    service.mcp_tools = tools
                application.state.mcp_last_checked_at = monotonic()
                application.state.mcp_checked = True

            await refresh_mcp()

            async def refresh_mcp_loop() -> None:
                """Repeat remote MCP discovery at the configured interval."""
                while True:
                    await asyncio.sleep(settings.mcp_refresh_seconds)
                    await refresh_mcp()

            mcp_refresh_task = asyncio.create_task(refresh_mcp_loop())
        if service:
            await service.start()
        elif af3_executor == "mock":
            async def advance_mock_jobs() -> None:
                """Advance in-process AF3 mock jobs while the API is running."""
                while True:
                    application.state.af3.advance(settings.mock_af3_seconds)
                    await asyncio.sleep(0.05)

            mock_scheduler = asyncio.create_task(advance_mock_jobs())
        try:
            yield
        finally:
            if mcp_refresh_task:
                mcp_refresh_task.cancel()
                try:
                    await mcp_refresh_task
                except asyncio.CancelledError:
                    pass
            if service:
                await service.stop()
            if mock_scheduler:
                mock_scheduler.cancel()
                try:
                    await mock_scheduler
                except asyncio.CancelledError:
                    pass
            if database is not None:
                database.close()

    app = FastAPI(title="PSKit Research Agent API", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(LoginRequired)
    async def login_required_handler(_request: Request, _error: LoginRequired) -> JSONResponse:
        """Map member-only capability rejection to a stable public error."""
        return JSONResponse(status_code=403, content={"detail": {"code": "LOGIN_REQUIRED"}})

    @app.exception_handler(GuestAccountDeleting)
    async def guest_account_deleting_handler(
        _request: Request, _error: GuestAccountDeleting,
    ) -> JSONResponse:
        """Tell a guest that cleanup has locked the account."""
        return JSONResponse(status_code=410, content={"detail": {"code": "GUEST_ACCOUNT_DELETING"}})
    app.state.metrics = RequestMetrics()
    app.add_middleware(ObservabilityMiddleware, metrics=app.state.metrics)
    app.state.settings = settings
    app.state.database = database
    app.state.pdf_processor = PdfProcessingPool(
        max_concurrent=settings.pdf_max_concurrent_parses,
        queue_timeout_seconds=settings.pdf_queue_timeout_seconds,
        parse_timeout_seconds=settings.pdf_parse_timeout_seconds,
        shared=PdfCapacityStore(storage, settings.pdf_max_concurrent_parses)
        if settings.agent_runtime == "pi" else None,
    )
    app.state.af3_executor = af3_executor
    app.state.mcp_executor = mcp_executor
    app.state.mcp_unavailable = False
    app.state.mcp_checked = False
    app.state.mcp_last_checked_at = None
    app.state.oauth_flows = auth.OAuthFlowStore(
        database
    )
    app.state.demo_store = DemoStore()
    app.state.identity_provider = (
        MockIdentityProvider(app.state.demo_store)
        if settings.mode == "mock"
        else SupabaseIdentityAdapter(settings.supabase_url, settings.supabase_publishable_key)
    )
    if settings.agent_runtime not in {"mock", "pi"}:
        raise ValueError("RESEARCH_AGENT_RUNTIME must be mock or pi")
    app.state.conversations = (
        ConversationStore()
        if settings.agent_runtime == "mock"
        else PersistentConversationStore(
            storage, af3_min_gpu_memory_mb=settings.af3_min_gpu_memory_mb,
        )
    )
    app.state.tool_runs = ToolRunStore(storage if settings.agent_runtime == "pi" else None)
    app.state.mcp_tool_calls = McpToolCallStore(
        storage if settings.agent_runtime == "pi" else ":memory:"
    )
    if mcp_servers and mcp_provider is None:
        mcp_provider = MultiRemoteMcp({
            server.id: RemoteMcp(
                server.url, allowed_tools=set(server.allowed_tools),
                bearer_token=os.environ.get(server.bearer_token_env, "") if server.bearer_token_env else "",
                timeout_seconds=settings.mcp_timeout_seconds,
            )
            for server in mcp_servers
        })
    app.state.mcp = (mcp_provider if mcp_provider is not None else
                     MockMcp() if mcp_executor == "mock" else
                     RemoteMcp(settings.mcp_url, allowed_tools=settings.mcp_allowed_tools(),
                               bearer_token=settings.mcp_bearer_token,
                               timeout_seconds=settings.mcp_timeout_seconds)
                     if mcp_executor == "remote" else DisabledMcp())
    if mcp_executor == "remote":
        app.state.mcp = LimitedMcp(
            app.state.mcp, max_calls=settings.mcp_max_concurrent_calls,
            queue_timeout_seconds=settings.mcp_queue_timeout_seconds,
            shared=McpCapacityStore(storage, settings.mcp_max_concurrent_calls)
            if settings.agent_runtime == "pi" else None,
            lease_seconds=max(10, settings.mcp_timeout_seconds + 5),
        )
    app.state.agent_service = (
        AgentService(
            app.state.conversations, pi_runner, settings.internal_api_url,
            settings.mock_af3_seconds, settings.resume_retry_seconds,
            model_gateway_api_key=settings.model_gateway_api_key or None,
            requires_gateway_key=settings.mode == "live",
            model_gateway_proxy_enabled=settings.mode == "live",
            mcp_tools=app.state.mcp.tools(),
            af3_executor=af3_executor,
            af3_queue_timeout_seconds=settings.af3_queue_timeout_seconds,
            af3_execution_timeout_seconds=settings.af3_execution_timeout_seconds,
            max_active_runs=settings.pi_max_active_runs,
            max_active_runs_per_user=settings.pi_max_active_runs_per_user,
            tool_token_secret=load_or_create_internal_tool_secret(storage),
            mcp_tool_calls=app.state.mcp_tool_calls,
        )
        if settings.agent_runtime == "pi" and pi_runner is not None
        else None
    )
    app.state.catalog = CatalogStore(
        mcp_tools=app.state.mcp.tools(),
        db_path=storage if settings.agent_runtime == "pi" else None,
        available_tools={tool.name for tool in app.state.mcp.tools()}
        | ({"submit_af3"} if af3_executor != "disabled" else set()),
        defer_unknown_tool_validation=mcp_executor == "remote",
    )
    app.state.identity_policy = IdentityPolicyStore(
        storage
        if settings.mode == "live" or settings.agent_runtime == "pi" else ":memory:",
        guest_token_limit=settings.guest_monthly_token_limit,
        guest_gpu_limit=settings.guest_daily_gpu_minute_limit,
        member_token_limit=settings.member_monthly_token_limit,
        member_gpu_limit=settings.member_daily_gpu_minute_limit,
        guest_max_active_runs=settings.guest_max_active_runs,
    )
    app.state.guest_capabilities = GuestCapabilityPolicy(
        app.state.identity_policy, settings.guest_mcp_allowed_tools(),
    )
    app.state.catalog.guest_capabilities = app.state.guest_capabilities
    app.state.catalog.identity_policy = app.state.identity_policy
    app.state.catalog.guest_file_limit_bytes = settings.guest_file_limit_bytes
    app.state.catalog.guest_storage_limit_bytes = settings.guest_storage_limit_bytes
    if app.state.agent_service is not None:
        app.state.agent_service.guest_capabilities = app.state.guest_capabilities
    if settings.agent_runtime == "pi":
        app.state.conversations.identity_policy = app.state.identity_policy
    app.state.guest_rate_limiter = GuestRateLimiter(
        storage if settings.mode == "live" else ":memory:",
        secret=settings.anonymous_rate_secret or "pskit-mock-anonymous-rate-secret",
        limit_per_hour=settings.anonymous_rate_limit_per_hour,
    ) if settings.effective_anonymous_enabled() else None
    app.state.quotas = (
        QuotaLedger() if settings.agent_runtime == "mock" else app.state.conversations
    )
    if settings.agent_runtime == "mock":
        app.state.quotas.identity_policy = app.state.identity_policy
    for user_id, limit in settings.user_token_limits().items():
        app.state.quotas.seed_token_limit(user_id, limit)
    app.state.af3 = (
        DisabledAf3() if af3_executor == "disabled" else
        MockAf3(app.state.quotas, app.state.conversations)
        if settings.agent_runtime == "mock" else
        PersistentMockAf3(app.state.conversations, simulation=af3_executor == "mock")
    )
    app.include_router(auth.router)
    app.include_router(guest_auth.router)
    app.include_router(usage.router)
    app.include_router(runs.router)
    app.include_router(capabilities.router)
    app.include_router(catalog.router)
    app.include_router(workspace.router)
    app.include_router(internal.router)
    app.include_router(admin.router)
    app.include_router(health.router)
    app.include_router(metrics.router)
    return app


app = create_app()
