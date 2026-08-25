from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import agent, auth, doctor, files, health, rag, tasks, tools
from app.config import get_settings
from app.db.session import init_db
from app.middleware.auth_abuse import AuthAbuseMiddleware
from app.security_headers import SecurityHeadersMiddleware


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        yield

    app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
    app.add_middleware(AuthAbuseMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    if settings.app_env != "production":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(doctor.router)
    app.include_router(tools.router)
    app.include_router(rag.router)
    app.include_router(agent.router)
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
            return FileResponse(dist_dir / "index.html")

    return app


app = create_app()
