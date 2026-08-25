from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import admin, agent, auth, doctor, files, harness, health, rag, research, tasks, tools
from app.config import get_settings
from app.db.session import init_db
from app.middleware.auth_abuse import AuthAbuseMiddleware
from app.security_headers import SecurityHeadersMiddleware
from app.harness.migration_bridge import (
    AgentRuntimeBridge,
    get_agent_runtime_bridge,
    install_agent_runtime_bridge,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时只接收受控注入的 Harness bridge，默认保持旧路线。"""

    previous_bridge = get_agent_runtime_bridge()
    production_runtime = getattr(app.state, "production_runtime", None)
    install_runtime = isinstance(production_runtime, AgentRuntimeBridge)
    if install_runtime:
        # wzf：不从环境变量或请求体拼装运行时；只有受控启动层显式传入的
        # AgentRuntimeBridge 才能进入 API/Agent 进程，缺失时保留默认 legacy。
        install_agent_runtime_bridge(production_runtime)
    try:
        init_db()
        yield
    finally:
        if install_runtime:
            # wzf：无论初始化、服务退出还是 lifespan 异常，都恢复原 bridge，
            # 避免测试/多应用装配把一次显式 composition 泄漏到后续请求。
            install_agent_runtime_bridge(previous_bridge)


def create_app(*, production_runtime: AgentRuntimeBridge | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
    # 仅保存受控启动层传入的对象；真正安装由 lifespan 完成，默认 None 不改变
    # migration_bridge 的默认关闭状态。
    app.state.production_runtime = production_runtime
    app.add_middleware(
        AuthAbuseMiddleware,
        login_limit=settings.auth_login_limit,
        register_limit=settings.auth_register_limit,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    if settings.app_env.lower() not in {"production", "release"}:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(doctor.router)
    app.include_router(tools.router)
    app.include_router(rag.router)
    app.include_router(agent.router)
    app.include_router(harness.router)
    app.include_router(research.router)
    app.include_router(tasks.router)
    app.include_router(files.router)

    dist_dir = settings.frontend_dist_dir
    assets_dir = dist_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    if (dist_dir / "index.html").exists():
        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str):
            candidate = (dist_dir / full_path).resolve()
            dist_root = dist_dir.resolve()
            if candidate.is_file() and (dist_root in candidate.parents or candidate == dist_root):
                return FileResponse(candidate)
            if full_path == "api" or full_path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return FileResponse(dist_dir / "index.html")

    return app


app = create_app()
