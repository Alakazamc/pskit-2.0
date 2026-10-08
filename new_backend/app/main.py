import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from shutil import which
from time import monotonic
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.adapters.disabled import DisabledAf3, DisabledMcp, DisabledWorkspaceSandboxProvider
from app.adapters.live.limited_mcp import LimitedMcp
from app.adapters.live.multi_remote_mcp import MultiRemoteMcp
from app.adapters.live.opensandbox_workspace import OpenSandboxWorkspaceProvider
from app.adapters.live.pi_rpc import PiRpcRunner
from app.adapters.live.remote_mcp import RemoteMcp
from app.adapters.live.sandbox_pi import SandboxPiRunner
from app.adapters.live.supabase_auth import SupabaseIdentityAdapter
from app.adapters.live.supabase_storage import SupabaseAvatarStorage
from app.adapters.live.turnstile import TurnstileVerifier
from app.adapters.mock.af3 import MockAf3
from app.adapters.mock.auth import MockIdentityProvider
from app.adapters.mock.avatars import MockAvatarStorage
from app.adapters.mock.mcp import MockMcp
from app.adapters.mock.persistent_af3 import PersistentMockAf3
from app.api import (
    admin,
    admin_auth,
    admin_models,
    admin_operations,
    admin_services,
    admin_tool_products,
    auth,
    capabilities,
    catalog,
    compute,
    guest_auth,
    health,
    internal,
    internal_compute,
    internal_workspace,
    metrics,
    models,
    profile,
    runs,
    sandbox_files,
    tool_products,
    usage,
    workspace,
)
from app.config import Settings
from app.db.postgres import PostgresDatabase
from app.domain.admin.audit import AdminOperations
from app.domain.admin.model_policy import ModelPolicy
from app.domain.admin.qualification import McpQualification
from app.domain.admin.releases import ConfigReleaseService
from app.domain.admin.roles import AdminStore
from app.domain.admin.service_endpoints import ApprovedEndpointPolicy
from app.domain.auth_abuse import AuthAbuseGuard
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
from app.domain.sandboxes import SandboxArtifactStore
from app.domain.store import DemoStore
from app.domain.tool_products.registry import ToolProductRegistry
from app.domain.tool_products.repository import ToolProductRepository
from app.domain.tool_products.runs import ToolRunGateway
from app.domain.tool_runs import ToolRunStore
from app.domain.workspace_attempts import WorkspaceAttemptStore
from app.domain.workspace_sandboxes import WorkspaceSandboxStore
from app.ports.avatars import AvatarStorage
from app.ports.captcha import CaptchaVerifier
from app.ports.providers import ProviderUnavailable
from app.ports.workspace_sandbox import WorkspaceProviderError
from app.services.agent import AgentService
from app.services.auth_protection import AuthProtection
from app.services.client_ip import TrustedClientIpResolver
from app.services.csrf import CsrfProtector
from app.services.model_catalog import ModelCatalog
from app.services.observability import ObservabilityMiddleware, RequestMetrics
from app.services.pdf_processing import PdfProcessingPool
from app.services.sandbox_operations import (
    LegacySandboxOperations,
    SandboxOperations,
    WorkspaceLeaseReconciler,
)
from app.services.session_titles import SessionTitleService
from app.services.workspace_files import WorkspaceFiles
from app.services.workspace_transfer import WorkspaceTransfer


def create_app(
    settings: Settings | None = None,
    *,
    pi_runner=None,
    mcp_provider=None,
    avatar_storage: AvatarStorage | None = None,
    captcha_verifier: CaptchaVerifier | None = None,
    auth_guard: AuthAbuseGuard | None = None,
) -> FastAPI:
    """Build the API with validated mock or live adapters.

    Args:
        settings: Explicit runtime settings; environment settings when absent.
        pi_runner: Optional injected Pi RPC runner for integration tests.
        mcp_provider: Optional injected MCP provider.
        avatar_storage: Optional private object-storage adapter for integration tests.

    Returns:
        Configured FastAPI app with stateful stores and routes.

    Raises:
        ValueError: A selected runtime lacks required or safe configuration.
    """
    settings = settings or Settings.from_env()
    settings.validate_workspace_config()
    if settings.mode not in {"mock", "live"}:
        raise ValueError("RESEARCH_AGENT_MODE must be mock or live")
    if not 1 <= settings.title_timeout_seconds <= 60:
        raise ValueError("RESEARCH_AGENT_TITLE_TIMEOUT_SECONDS must be between 1 and 60")
    if settings.model_policy_mode not in {"legacy", "managed"}:
        raise ValueError("RESEARCH_AGENT_MODEL_POLICY_MODE must be legacy or managed")
    approved_references = {}
    for kind in ("endpoints", "credentials"):
        name = f"RESEARCH_AGENT_ADMIN_SERVICE_{kind.upper()}_JSON"
        try:
            references = json.loads(getattr(settings, f"admin_service_{kind}_json"))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{name} must contain a JSON object") from exc
        if not isinstance(references, dict):
            raise ValueError(f"{name} must contain a JSON object")  # noqa: TRY004 — invalid environment configuration
        for key, value in references.items():
            valid_endpoint = (
                kind == "endpoints"
                and isinstance(value, dict)
                and isinstance(value.get("url"), str)
            )
            if (
                not isinstance(key, str)
                or not key
                or not (isinstance(value, str) or valid_endpoint)
            ):
                raise ValueError(f"{name} contains an invalid approved reference")
        approved_references[kind] = references
    try:
        admin_mcp_network_zones = json.loads(settings.admin_mcp_network_zones_json)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "RESEARCH_AGENT_ADMIN_MCP_NETWORK_ZONES_JSON must contain a JSON object"
        ) from exc
    if not isinstance(admin_mcp_network_zones, dict):
        raise ValueError(  # noqa: TRY004 — invalid environment configuration
            "RESEARCH_AGENT_ADMIN_MCP_NETWORK_ZONES_JSON must contain a JSON object"
        )
    endpoint_policy = ApprovedEndpointPolicy(admin_mcp_network_zones)
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
                raise ValueError(
                    f"Missing MCP credential environment variable: {server.bearer_token_env}"
                )
    if (
        mcp_executor == "remote"
        and not settings.mcp_url
        and not mcp_servers
        and mcp_provider is None
    ):
        raise ValueError("RESEARCH_AGENT_MCP_URL is required in remote mode")
    if (
        mcp_executor == "remote"
        and not mcp_servers
        and mcp_provider is None
        and not settings.mcp_allowed_tools()
    ):
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
    if (
        settings.pi_max_active_runs < 1
        or settings.pi_max_active_runs_per_user < 1
        or settings.pi_max_active_runs_per_user > settings.pi_max_active_runs
    ):
        raise ValueError("Pi concurrency limits must be positive and per-user <= global")
    if (
        min(
            settings.guest_monthly_token_limit,
            settings.guest_daily_gpu_minute_limit,
            settings.member_monthly_token_limit,
            settings.member_daily_gpu_minute_limit,
        )
        < 0
    ):
        raise ValueError("Account Token and GPU limits must be nonnegative")
    if settings.guest_max_active_runs < 1:
        raise ValueError("RESEARCH_AGENT_GUEST_MAX_ACTIVE_RUNS must be positive")
    if settings.guest_file_limit_bytes < 1 or settings.guest_storage_limit_bytes < 0:
        raise ValueError("Guest file limit must be positive and storage limit nonnegative")
    if af3_executor == "callback" and settings.agent_runtime != "pi":
        raise ValueError("Callback AF3 execution requires the persistent Pi runtime")
    if af3_executor == "callback" and not settings.compute_callback_key:
        raise ValueError("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY is required in callback mode")
    if (
        af3_executor == "callback"
        and settings.mode == "live"
        and settings.af3_min_gpu_memory_mb == 0
    ):
        raise ValueError("RESEARCH_AGENT_AF3_MIN_GPU_MEMORY_MB is required for live AF3 callbacks")
    if settings.mode == "live":
        settings.require_live_config()
    if settings.anonymous_rate_limit_per_hour < 1:
        raise ValueError("RESEARCH_AGENT_ANON_RATE_LIMIT_PER_HOUR must be positive")
    if settings.mode == "live" and settings.effective_anonymous_enabled():
        if len(settings.anonymous_rate_secret) < 16:
            raise ValueError("RESEARCH_AGENT_ANON_RATE_SECRET must have at least 16 characters")
        if not settings.anonymous_captcha_required:
            raise ValueError(
                "RESEARCH_AGENT_ANONYMOUS_CAPTCHA_REQUIRED must be enabled in live mode"
            )
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
            executable = (
                str(local_pi)
                if settings.pi_executable == "pi" and local_pi.is_file()
                else settings.pi_executable
            )
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
                model_gateway_base_url=(
                    f"{settings.internal_api_url.rstrip('/')}/internal/model"
                    if settings.mode == "live"
                    else None
                ),
                model_gateway_model=settings.model_gateway_model or None,
                system_prompt=(
                    Path(__file__).resolve().parents[1] / "pi" / "system-prompt.md"
                ).read_text(),
            )
    database = (
        PostgresDatabase(settings.database_url, schema=settings.database_schema)
        if settings.mode == "live"
        else None
    )
    storage = database if database is not None else settings.agent_db_path
    workspace_sandbox_store = None
    workspace_reconciler = None
    if settings.workspace_provider == "opensandbox":
        if database is None:
            raise ValueError("OpenSandbox workspace provider requires live PostgreSQL")
        workspace_sandbox_store = WorkspaceSandboxStore(database)
        workspace_sandbox_provider = OpenSandboxWorkspaceProvider(
            settings,
            workspace_sandbox_store,
        )
        workspace_reconciler = WorkspaceLeaseReconciler(
            workspace_sandbox_store,
            workspace_sandbox_provider.lifecycle.reconcile_orphans,
        )
        sandbox_operations = SandboxOperations(
            workspace_sandbox_provider,
            workspace_sandbox_store,
        )
    else:
        workspace_sandbox_provider = DisabledWorkspaceSandboxProvider()
        sandbox_operations = (
            LegacySandboxOperations(
                settings.sandbox_manager_url,
                settings.sandbox_manager_token,
            )
            if settings.pi_execution == "sandbox"
            and settings.sandbox_manager_url
            and settings.sandbox_manager_token
            else None
        )
    request_metrics = RequestMetrics()
    configured_auth_guard = auth_guard
    if settings.auth_abuse_mode != "off" and configured_auth_guard is None:
        if database is None:
            raise ValueError("Auth abuse protection requires live PostgreSQL")
        configured_auth_guard = AuthAbuseGuard(
            database,
            secret=settings.auth_rate_limit_secret,
        )
    configured_captcha = captcha_verifier
    if settings.auth_captcha_required and configured_captcha is None:
        configured_captcha = TurnstileVerifier(
            settings.turnstile_secret_key,
            settings.turnstile_hostnames(),
        )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Start discovery and background schedulers, then release them on exit."""
        service = application.state.agent_service
        mock_scheduler = None
        mcp_refresh_task = None
        auth_cleanup_task = None
        workspace_probe_task = None
        if settings.workspace_provider == "opensandbox":

            async def probe_workspace() -> None:
                """Populate verified capabilities without creating a user workspace."""
                try:
                    await application.state.workspace_sandbox_provider.probe()
                except WorkspaceProviderError:
                    # Readiness publishes the sanitized degraded/unsafe state.
                    pass
                else:
                    await application.state.workspace_lease_reconciler.start()

            workspace_probe_task = asyncio.create_task(probe_workspace())
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
        if application.state.session_titles:
            await application.state.session_titles.start()
        if application.state.auth_guard is not None:

            async def cleanup_auth_abuse() -> None:
                """Bound stale auth state without delaying request admission."""
                while True:
                    await asyncio.sleep(3600)
                    await asyncio.to_thread(application.state.auth_guard.cleanup_expired, 1000)

            auth_cleanup_task = asyncio.create_task(cleanup_auth_abuse())
        try:
            yield
        finally:
            if application.state.workspace_lease_reconciler is not None:
                await application.state.workspace_lease_reconciler.stop()
            if workspace_probe_task is not None:
                if not workspace_probe_task.done():
                    workspace_probe_task.cancel()
                try:
                    await workspace_probe_task
                except asyncio.CancelledError:
                    pass
            if auth_cleanup_task:
                auth_cleanup_task.cancel()
                try:
                    await auth_cleanup_task
                except asyncio.CancelledError:
                    pass
            if application.state.session_titles:
                await application.state.session_titles.stop()
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

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        """Return stable public codes for management and attachment validation."""
        if request.url.path.startswith("/api/v1/admin/"):
            return JSONResponse(
                status_code=422, content={"detail": {"code": "ADMIN_VALIDATION_FAILED"}}
            )
        if any(
            item["type"] == "too_long" and tuple(item["loc"]) == ("body", "attachments")
            for item in error.errors()
        ):
            return JSONResponse(
                status_code=422, content={"detail": {"code": "TOO_MANY_ATTACHMENTS"}}
            )
        return await request_validation_exception_handler(request, error)

    @app.exception_handler(LoginRequired)
    async def login_required_handler(_request: Request, _error: LoginRequired) -> JSONResponse:
        """Map member-only capability rejection to a stable public error."""
        return JSONResponse(status_code=403, content={"detail": {"code": "LOGIN_REQUIRED"}})

    @app.exception_handler(GuestAccountDeleting)
    async def guest_account_deleting_handler(
        _request: Request,
        _error: GuestAccountDeleting,
    ) -> JSONResponse:
        """Tell a guest that cleanup has locked the account."""
        return JSONResponse(status_code=410, content={"detail": {"code": "GUEST_ACCOUNT_DELETING"}})

    app.state.metrics = request_metrics
    app.add_middleware(ObservabilityMiddleware, metrics=app.state.metrics)
    app.state.settings = settings
    csrf_secret = settings.effective_auth_csrf_secret() or secrets.token_urlsafe(32)
    frontend = urlparse(settings.frontend_url)
    app.state.csrf = CsrfProtector(
        csrf_secret,
        f"{frontend.scheme}://{frontend.netloc}",
    )
    app.state.database = database
    app.state.workspace_sandbox_store = workspace_sandbox_store
    app.state.workspace_sandbox_provider = workspace_sandbox_provider
    app.state.workspace_lease_reconciler = workspace_reconciler
    app.state.workspace_attempts = WorkspaceAttemptStore(database) if database is not None else None
    app.state.workspace_files = (
        WorkspaceFiles(workspace_sandbox_provider)
        if settings.workspace_provider == "opensandbox"
        else None
    )
    app.state.tool_product_repository = (
        ToolProductRepository(database) if database is not None else None
    )
    app.state.tool_product_registry = (
        ToolProductRegistry(app.state.tool_product_repository)
        if app.state.tool_product_repository is not None
        else None
    )
    app.state.mcp_qualification = (
        McpQualification(
            database,
            app.state.tool_product_repository,
            endpoint_policy,
            credential_refs=approved_references["credentials"],
            timeout_seconds=min(settings.mcp_timeout_seconds, 30),
            execution_timeout_seconds=min(settings.mcp_timeout_seconds, 1800),
        )
        if database is not None
        else None
    )
    app.state.auth_guard = configured_auth_guard
    app.state.auth_protection = AuthProtection(
        guard=configured_auth_guard,
        captcha=configured_captcha,
        resolver=TrustedClientIpResolver(settings.auth_trusted_proxy_cidrs()),
        metrics=request_metrics,
        mode=settings.auth_abuse_mode,
        captcha_required=settings.auth_captcha_required,
    )
    app.state.compute_jobs = None
    app.state.compute_leases = None
    app.state.compute_events = None
    if settings.compute_enabled:
        if database is None or settings.agent_runtime != "pi":
            raise ValueError(
                "Generic compute requires live PostgreSQL and the persistent Pi runtime"
            )
        from app.contracts.compute import ComputeServiceManifest
        from app.domain.compute.events import ComputeEvents
        from app.domain.compute.jobs import ComputeJobs

        app.state.compute_jobs = ComputeJobs(database)
        app.state.compute_events = ComputeEvents(database)
        app.state.compute_jobs.events = app.state.compute_events
        for manifest in json.loads(settings.compute_services_json):
            app.state.compute_jobs.catalog.register(ComputeServiceManifest.model_validate(manifest))
    app.state.tool_run_gateway = (
        ToolRunGateway(
            database,
            app.state.tool_product_repository,
            app.state.tool_product_registry,
            app.state.compute_jobs,
            compute_events=app.state.compute_events,
        )
        if app.state.compute_jobs is not None
        else None
    )
    app.state.pdf_processor = PdfProcessingPool(
        max_concurrent=settings.pdf_max_concurrent_parses,
        queue_timeout_seconds=settings.pdf_queue_timeout_seconds,
        parse_timeout_seconds=settings.pdf_parse_timeout_seconds,
        shared=PdfCapacityStore(storage, settings.pdf_max_concurrent_parses)
        if settings.agent_runtime == "pi"
        else None,
    )
    app.state.af3_executor = af3_executor
    app.state.mcp_executor = mcp_executor
    app.state.mcp_unavailable = False
    app.state.mcp_checked = False
    app.state.mcp_last_checked_at = None
    app.state.oauth_flows = auth.OAuthFlowStore(database)
    app.state.demo_store = DemoStore()
    app.state.identity_provider = (
        MockIdentityProvider(app.state.demo_store)
        if settings.mode == "mock"
        else SupabaseIdentityAdapter(settings.supabase_url, settings.supabase_publishable_key)
    )
    app.state.avatar_storage = avatar_storage or (
        MockAvatarStorage()
        if settings.mode == "mock"
        else SupabaseAvatarStorage(settings.supabase_url, settings.supabase_secret_key)
    )
    app.state.avatar_processing = asyncio.Semaphore(2)
    if settings.agent_runtime not in {"mock", "pi"}:
        raise ValueError("RESEARCH_AGENT_RUNTIME must be mock or pi")
    app.state.conversations = (
        ConversationStore()
        if settings.agent_runtime == "mock"
        else PersistentConversationStore(
            storage,
            af3_min_gpu_memory_mb=settings.af3_min_gpu_memory_mb,
        )
    )
    app.state.tool_runs = ToolRunStore(storage if settings.agent_runtime == "pi" else None)
    app.state.mcp_tool_calls = McpToolCallStore(
        storage if settings.agent_runtime == "pi" else ":memory:"
    )
    if mcp_servers and mcp_provider is None:
        mcp_provider = MultiRemoteMcp(
            {
                server.id: RemoteMcp(
                    server.url,
                    allowed_tools=set(server.allowed_tools),
                    bearer_token=os.environ.get(server.bearer_token_env, "")
                    if server.bearer_token_env
                    else "",
                    timeout_seconds=settings.mcp_timeout_seconds,
                )
                for server in mcp_servers
            }
        )
    app.state.mcp = (
        mcp_provider
        if mcp_provider is not None
        else MockMcp()
        if mcp_executor == "mock"
        else RemoteMcp(
            settings.mcp_url,
            allowed_tools=settings.mcp_allowed_tools(),
            bearer_token=settings.mcp_bearer_token,
            timeout_seconds=settings.mcp_timeout_seconds,
        )
        if mcp_executor == "remote"
        else DisabledMcp()
    )
    if mcp_executor == "remote":
        app.state.mcp = LimitedMcp(
            app.state.mcp,
            max_calls=settings.mcp_max_concurrent_calls,
            queue_timeout_seconds=settings.mcp_queue_timeout_seconds,
            shared=McpCapacityStore(storage, settings.mcp_max_concurrent_calls)
            if settings.agent_runtime == "pi"
            else None,
            lease_seconds=max(10, settings.mcp_timeout_seconds + 5),
        )
    app.state.agent_service = (
        AgentService(
            app.state.conversations,
            pi_runner,
            settings.internal_api_url,
            settings.mock_af3_seconds,
            settings.resume_retry_seconds,
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
    app.state.model_catalog = ModelCatalog(
        base_url=settings.model_gateway_base_url or settings.new_api_base_url,
        api_key=settings.model_gateway_api_key,
        default_model=settings.model_gateway_model
        or settings.new_api_model
        or settings.pi_model
        or "mock-model",
        image_model_ids=settings.model_gateway_image_models(),
    )
    app.state.identity_policy = IdentityPolicyStore(
        storage if settings.mode == "live" or settings.agent_runtime == "pi" else ":memory:",
        guest_token_limit=settings.guest_monthly_token_limit,
        guest_gpu_limit=settings.guest_daily_gpu_minute_limit,
        member_token_limit=settings.member_monthly_token_limit,
        member_gpu_limit=settings.member_daily_gpu_minute_limit,
        guest_max_active_runs=settings.guest_max_active_runs,
    )
    app.state.guest_capabilities = GuestCapabilityPolicy(
        app.state.identity_policy,
        settings.guest_mcp_allowed_tools(),
    )
    app.state.catalog.guest_capabilities = app.state.guest_capabilities
    app.state.catalog.identity_policy = app.state.identity_policy
    app.state.catalog.guest_file_limit_bytes = settings.guest_file_limit_bytes
    app.state.catalog.guest_storage_limit_bytes = settings.guest_storage_limit_bytes
    if app.state.agent_service is not None:
        app.state.agent_service.guest_capabilities = app.state.guest_capabilities
        app.state.agent_service.catalog = app.state.catalog
    if settings.agent_runtime == "pi":
        app.state.conversations.identity_policy = app.state.identity_policy
    app.state.guest_rate_limiter = (
        GuestRateLimiter(
            storage if settings.mode == "live" else ":memory:",
            secret=settings.anonymous_rate_secret or "pskit-mock-anonymous-rate-secret",
            limit_per_hour=settings.anonymous_rate_limit_per_hour,
        )
        if settings.effective_anonymous_enabled()
        else None
    )
    app.state.quotas = (
        QuotaLedger() if settings.agent_runtime == "mock" else app.state.conversations
    )
    if settings.agent_runtime == "mock":
        app.state.quotas.identity_policy = app.state.identity_policy
    for user_id, limit in settings.user_token_limits().items():
        app.state.quotas.seed_token_limit(user_id, limit)
    app.state.admin_store = AdminStore(
        storage if settings.agent_runtime == "pi" or settings.mode == "live" else ":memory:",
        identity_policy=app.state.identity_policy,
        quotas=app.state.quotas,
    )
    app.state.model_policy = ModelPolicy(
        app.state.admin_store,
        app.state.model_catalog,
        managed=settings.model_policy_mode == "managed",
    )
    app.state.session_titles = (
        SessionTitleService(
            app.state.conversations,
            app.state.model_policy,
            model=settings.title_model,
            timeout=settings.title_timeout_seconds,
            gateway_kind=settings.model_gateway_kind,
        )
        if settings.agent_runtime == "pi"
        else None
    )
    app.state.catalog.admin_storage_limit_for = app.state.admin_store.storage_limit_for
    app.state.conversations.admin_concurrency_limit_for = (
        app.state.admin_store.concurrency_limit_for
    )
    app.state.admin_releases = ConfigReleaseService(
        app.state.admin_store,
        endpoints=approved_references["endpoints"],
        credentials=approved_references["credentials"],
    )
    app.state.sandbox_operations = sandbox_operations
    app.state.workspace_transfer = None
    if (
        database is not None
        and settings.workspace_provider == "opensandbox"
        and app.state.agent_service
    ):
        artifact_store = SandboxArtifactStore(app.state.conversations.db)
        artifact_store.storage_limit_for = app.state.catalog.total_storage_limit_for
        app.state.workspace_transfer = WorkspaceTransfer(
            app.state.catalog,
            workspace_sandbox_provider,
            artifact_store,
            conversations=app.state.conversations,
        )
        app.state.agent_service.workspace_transfer = app.state.workspace_transfer
    if app.state.compute_jobs is not None:
        from app.domain.compute.ledger import ComputeLedger

        app.state.compute_jobs.ledger = ComputeLedger(
            database,
            cpu_daily_limit_ms=settings.compute_cpu_daily_limit_ms,
            gpu_limit_for=app.state.conversations._gpu_limit_for,
        )
    if app.state.compute_jobs is not None:
        from app.domain.compute.leases import ComputeLeases

        app.state.compute_leases = ComputeLeases(
            database,
            app.state.compute_jobs.ledger,
            events=app.state.compute_events,
            tool_runs=app.state.tool_run_gateway,
        )
        app.state.compute_jobs.admin_concurrency_limit_for = (
            app.state.admin_store.concurrency_limit_for
        )
    app.state.admin_operations = AdminOperations(
        app.state.admin_store,
        compute_jobs=app.state.compute_jobs,
        sandbox_operations=app.state.sandbox_operations,
        default_concurrency_limit=settings.pi_max_active_runs_per_user,
    )
    if app.state.agent_service is not None:
        app.state.agent_service.compute_jobs = app.state.compute_jobs
        app.state.agent_service.compute_leases = app.state.compute_leases
    app.state.af3 = (
        DisabledAf3()
        if af3_executor == "disabled"
        else MockAf3(app.state.quotas, app.state.conversations)
        if settings.agent_runtime == "mock"
        else PersistentMockAf3(app.state.conversations, simulation=af3_executor == "mock")
    )
    app.include_router(auth.router)
    app.include_router(profile.router)
    app.include_router(guest_auth.router)
    app.include_router(usage.router)
    app.include_router(compute.router)
    app.include_router(runs.router)
    app.include_router(capabilities.router)
    app.include_router(catalog.router)
    app.include_router(models.router)
    app.include_router(workspace.router)
    app.include_router(internal.router)
    app.include_router(internal_compute.router)
    app.include_router(internal_workspace.router)
    app.include_router(admin.router)
    app.include_router(admin_auth.router)
    app.include_router(admin_models.router)
    app.include_router(admin_services.router)
    app.include_router(admin_operations.router)
    app.include_router(admin_tool_products.router)
    app.include_router(sandbox_files.router)
    app.include_router(tool_products.router)
    app.include_router(tool_products.tool_run_router)
    app.include_router(health.router)
    app.include_router(metrics.router)
    return app


app = create_app()
