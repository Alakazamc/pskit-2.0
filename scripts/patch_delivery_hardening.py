"""Apply delivery hardening to the durable PSKit backend image.

The science image is the source of truth for the durable research workflow.
This patch intentionally makes small, asserted edits instead of replacing that
backend with the older repository snapshot.
"""

from __future__ import annotations

from pathlib import Path
import sys


def replace_once(source: str, old: str, new: str, *, label: str) -> str:
    if new in source:
        return source
    if old not in source:
        raise SystemExit(f"refusing patch: {label} anchor not found")
    return source.replace(old, new, 1)


def patch_main(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "from fastapi.responses import FileResponse\n",
        "from fastapi.responses import FileResponse, JSONResponse\n",
        label="FastAPI response import",
    )
    source = replace_once(
        source,
        "from app.harness.migration_bridge import (\n",
        "from app.middleware.auth_abuse import AuthAbuseMiddleware\n"
        "from app.security_headers import SecurityHeadersMiddleware\n"
        "from app.harness.migration_bridge import (\n",
        label="middleware imports",
    )
    old_cors = """    app.add_middleware(
        CORSMiddleware,
        allow_origins=[\"http://localhost:5173\", \"http://127.0.0.1:5173\"],
        allow_credentials=True,
        allow_methods=[\"*\"],
        allow_headers=[\"*\"],
    )
"""
    new_cors = """    app.add_middleware(AuthAbuseMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    if settings.app_env.lower() not in {\"production\", \"release\"}:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[\"http://localhost:5173\", \"http://127.0.0.1:5173\"],
            allow_credentials=True,
            allow_methods=[\"*\"],
            allow_headers=[\"*\"],
        )
"""
    source = replace_once(source, old_cors, new_cors, label="CORS registration")
    source = replace_once(
        source,
        'app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)',
        'app = FastAPI(title=settings.app_name, version="0.2.2", lifespan=lifespan)',
        label="application version",
    )
    old_spa = """            if candidate.is_file() and (dist_root in candidate.parents or candidate == dist_root):
                return FileResponse(candidate)
            return FileResponse(dist_dir / \"index.html\")
"""
    new_spa = """            if candidate.is_file() and (dist_root in candidate.parents or candidate == dist_root):
                return FileResponse(candidate)
            if full_path == \"api\" or full_path.startswith(\"api/\"):
                return JSONResponse({\"detail\": \"Not Found\"}, status_code=404)
            return FileResponse(dist_dir / \"index.html\")
"""
    source = replace_once(source, old_spa, new_spa, label="SPA API boundary")
    path.write_text(source, encoding="utf-8")


def patch_config(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        '    qdrant_distance: str = "cosine"\n',
        '    qdrant_distance: str = "cosine"\n'
        '    readiness_require_qdrant: bool = False\n',
        label="Qdrant readiness setting",
    )
    path.write_text(source, encoding="utf-8")


def patch_health(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    source = source.replace('version="0.1.0"', 'version="0.2.2"')
    anchor = """    except Exception:
        checks.append(StatusItem(name=\"database\", status=\"fail\", detail=\"query failed\"))

    heartbeat = db.get(ServiceHeartbeat, \"task-worker\")
"""
    replacement = """    except Exception:
        checks.append(StatusItem(name=\"database\", status=\"fail\", detail=\"query failed\"))

    if settings.readiness_require_qdrant:
        try:
            from app.rag.qdrant_store import get_qdrant_client

            info = get_qdrant_client().get_collection(settings.qdrant_collection)
            points = getattr(info, \"points_count\", None)
            if not points:
                raise RuntimeError(\"knowledge collection is empty\")
            checks.append(
                StatusItem(name=\"qdrant\", status=\"ok\", detail=f\"{points} points\")
            )
        except Exception:
            checks.append(
                StatusItem(name=\"qdrant\", status=\"fail\", detail=\"collection unavailable\")
            )

    heartbeat = db.get(ServiceHeartbeat, \"task-worker\")
"""
    source = replace_once(source, anchor, replacement, label="Qdrant readiness check")
    path.write_text(source, encoding="utf-8")


def patch_sessions(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "import secrets\n",
        "import os\nimport secrets\n",
        label="sessions os import",
    )
    source = replace_once(
        source,
        "from sqlalchemy.orm import Session\n",
        "from sqlalchemy import select\nfrom sqlalchemy.orm import Session\n",
        label="sessions select import",
    )
    anchor = """    settings = get_settings()
    token = make_session_token()
"""
    replacement = """    settings = get_settings()
    now = now_utc()
    maximum = max(1, int(os.getenv(\"PSKIT_MAX_ACTIVE_SESSIONS_PER_USER\", \"5\")))
    active = db.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
    ).all()
    retained = []
    for existing in active:
        if ensure_utc(existing.expires_at) <= now:
            db.delete(existing)
        else:
            retained.append(existing)
    for existing in retained[maximum - 1 :]:
        db.delete(existing)
    token = make_session_token()
"""
    source = replace_once(source, anchor, replacement, label="session cap")
    expiry = """    expires_at = ensure_utc(session.expires_at)
    if expires_at < datetime.now(timezone.utc):
"""
    bounded_expiry = """    expires_at = ensure_utc(session.expires_at)
    issued_at = ensure_utc(session.created_at)
    configured_expiry = issued_at + timedelta(days=get_settings().session_ttl_days)
    expires_at = min(expires_at, configured_expiry)
    if expires_at < datetime.now(timezone.utc):
"""
    source = replace_once(source, expiry, bounded_expiry, label="existing session TTL bound")
    path.write_text(source, encoding="utf-8")


def patch_tasks(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "import json\n",
        "import json\nimport os\n",
        label="task quota os import",
    )
    source = replace_once(
        source,
        "from sqlalchemy import select\n",
        "from sqlalchemy import func, select\n",
        label="task quota func import",
    )
    anchor = """    if task_type not in WORKER_TASK_NAMES:
        raise ValueError(f\"Unsupported worker task type: {task_type}\")
    input_json = validate_task_input(task_type, input_json)
"""
    replacement = """    if task_type not in WORKER_TASK_NAMES:
        raise ValueError(f\"Unsupported worker task type: {task_type}\")
    input_json = validate_task_input(task_type, input_json)
"""
    source = replace_once(source, anchor, replacement, label="task validation anchor")
    quota_anchor = """            return existing
    task = Task(
"""
    quota_replacement = """            return existing
    active_statuses = (\"queued\", \"running\")
    if task_type == \"run_alphafold3\":
        limit = max(1, int(os.getenv(\"PSKIT_MAX_ACTIVE_AF3_PER_USER\", \"1\")))
        active = db.scalar(
            select(func.count()).select_from(Task).where(
                Task.user_id == user.id,
                Task.task_type == \"run_alphafold3\",
                Task.status.in_(active_statuses),
            )
        ) or 0
        if active >= limit:
            raise ValueError(\"Only one active AlphaFold3 task is allowed per user\")
    else:
        limit = max(1, int(os.getenv(\"PSKIT_MAX_ACTIVE_TASKS_PER_USER\", \"4\")))
        active = db.scalar(
            select(func.count()).select_from(Task).where(
                Task.user_id == user.id,
                Task.task_type != \"run_alphafold3\",
                Task.status.in_(active_statuses),
            )
        ) or 0
        if active >= limit:
            raise ValueError(\"Too many active scientific tasks for this user\")
    task = Task(
"""
    source = replace_once(
        source,
        quota_anchor,
        quota_replacement,
        label="per-user task quota",
    )
    path.write_text(source, encoding="utf-8")


def patch_artifacts(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    anchor = """    artifact = Artifact(
        user_id=user.id,
"""
    replacement = """    object_key = str(resolved.relative_to(root))
    existing = db.scalar(
        select(Artifact).where(
            Artifact.user_id == user.id,
            Artifact.storage_backend == \"local\",
            Artifact.object_key == object_key,
        )
    )
    if existing is not None:
        existing.session_id = session_id
        existing.task_id = task_id
        existing.kind = kind
        existing.filename = path.name
        existing.mime_type = mime_type
        existing.size_bytes = resolved.stat().st_size
        if commit:
            db.commit()
            db.refresh(existing)
        else:
            db.flush()
        return existing
    artifact = Artifact(
        user_id=user.id,
"""
    source = replace_once(source, anchor, replacement, label="artifact idempotency")
    source = replace_once(
        source,
        "        object_key=str(resolved.relative_to(root)),\n",
        "        object_key=object_key,\n",
        label="artifact object key reuse",
    )
    path.write_text(source, encoding="utf-8")


def patch_agent(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    fingerprint_anchor = """            name, raw_args = tool_call_name_and_args(call)
            fingerprint = tool_call_fingerprint(name, raw_args)
            if waiting_for_tasks and name not in LONG_RUNNING_TOOL_NAMES:
"""
    fingerprint_replacement = """            name, raw_args = tool_call_name_and_args(call)
            fingerprint = tool_call_fingerprint(name, raw_args)
            failed_fingerprints = {
                item.get(\"arguments_hash\")
                for item in diagnostics
                if isinstance(item, dict)
                and item.get(\"error_type\") == \"tool_execution_error\"
            }
            if fingerprint in failed_fingerprints:
                error = {
                    \"error_type\": \"duplicate_failed_tool_call\",
                    \"tool\": name,
                    \"arguments_hash\": fingerprint,
                    \"message\": \"相同参数的工具调用已经失败，本轮不再重复执行。\",
                }
                diagnostics.append(error)
                events.append(make_event(\"error\", error=error))
                messages.append(
                    {
                        \"role\": \"tool\",
                        \"tool_call_id\": call_id,
                        \"content\": json.dumps(error, ensure_ascii=False),
                    }
                )
                continue
            if waiting_for_tasks and name not in LONG_RUNNING_TOOL_NAMES:
"""
    source = replace_once(
        source,
        fingerprint_anchor,
        fingerprint_replacement,
        label="failed tool fingerprint suppression",
    )
    source = replace_once(
        source,
        '                    "detail_code": exc.__class__.__name__,\n',
        '                    "detail_code": exc.__class__.__name__,\n'
        '                    "arguments_hash": fingerprint,\n',
        label="failed tool fingerprint evidence",
    )
    old = """        if tool_failed:
            self.record.failed = True
            self.record.error_code = self.record.error_code or \"tool_execution_error\"
"""
    new = """        if tool_failed and not self.record.failed:
            # A tool-level failure is part of the scientific result, not a failed
            # message transport.  Preserve diagnostics while allowing the final
            # answer and any successful tools/artifacts to reach the UI normally.
            self.record.error_code = self.record.error_code or \"tool_execution_warning\"
"""
    source = replace_once(source, old, new, label="Agent partial-failure semantics")
    path.write_text(source, encoding="utf-8")


def patch_external_tools(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    anchor = """    if response.status_code >= 400:
        raise ToolExecutionError(f\"RCSB search failed: HTTP {response.status_code} {response.text[:300]}\")
    data = response.json()
    hits = [
"""
    replacement = """    if response.status_code >= 400:
        raise ToolExecutionError(f\"RCSB search failed: HTTP {response.status_code} {response.text[:300]}\")
    if response.status_code == 204 or not response.content:
        data = {\"result_set\": [], \"total_count\": 0}
    else:
        try:
            data = response.json()
        except ValueError as exc:
            raise ToolExecutionError(\"RCSB search returned invalid JSON\") from exc
    hits = [
"""
    source = replace_once(source, anchor, replacement, label="RCSB empty search result")
    path.write_text(source, encoding="utf-8")


def patch_auth(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    anchor = """@router.post(\"/register\", response_model=UserResponse)
def register(payload: AuthRequest, request: Request, response: Response, db: Session = Depends(get_db)):
"""
    replacement = """@router.get(\"/registration\")
def registration_status():
    return {\"enabled\": True, \"mode\": \"open\", \"first_user\": False}


@router.post(\"/register\", response_model=UserResponse)
def register(payload: AuthRequest, request: Request, response: Response, db: Session = Depends(get_db)):
"""
    source = replace_once(source, anchor, replacement, label="registration status endpoint")
    path.write_text(source, encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_delivery_hardening.py BACKEND_ROOT")
    root = Path(sys.argv[1])
    patch_main(root / "app" / "main.py")
    patch_config(root / "app" / "config.py")
    patch_health(root / "app" / "api" / "health.py")
    patch_sessions(root / "app" / "auth" / "sessions.py")
    patch_tasks(root / "app" / "tasks" / "service.py")
    patch_artifacts(root / "app" / "artifacts" / "service.py")
    patch_agent(root / "app" / "agent" / "orchestrator.py")
    patch_external_tools(root / "app" / "tools" / "external.py")
    patch_auth(root / "app" / "api" / "auth.py")


if __name__ == "__main__":
    main()
