"""Proxy-aware, cross-process guard for public authentication endpoints."""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.db.models import AuthRateLimitBucket, now_utc
from app.db.session import SessionLocal
from app.network import client_ip


logger = logging.getLogger(__name__)


class AuthAbuseMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        login_limit: int = 30,
        register_limit: int = 5,
        window_seconds: int = 60,
    ):
        super().__init__(app)
        self.login_limit = max(1, login_limit)
        self.register_limit = max(1, register_limit)
        self.window_seconds = max(1, window_seconds)

    def _limited(self, kind: str, ip: str) -> int | None:
        limit = self.login_limit if kind == "login" else self.register_limit
        now = now_utc()
        epoch = int(time.time())
        seconds_into_window = epoch % self.window_seconds
        window_start = epoch - seconds_into_window
        window_end = now + timedelta(seconds=self.window_seconds - seconds_into_window)
        raw_key = f"{kind}:{ip}:{window_start}".encode()
        bucket_key = hashlib.sha256(raw_key).hexdigest()
        db = SessionLocal()
        try:
            values = {
                "key": bucket_key,
                "count": 1,
                "window_ends_at": window_end,
                "created_at": now,
            }
            dialect = db.get_bind().dialect.name
            if dialect == "sqlite":
                statement = sqlite_insert(AuthRateLimitBucket).values(**values)
            elif dialect == "postgresql":
                statement = postgresql_insert(AuthRateLimitBucket).values(**values)
            else:
                raise RuntimeError(f"Unsupported rate-limit database: {dialect}")
            statement = statement.on_conflict_do_update(
                index_elements=[AuthRateLimitBucket.key],
                set_={"count": AuthRateLimitBucket.count + 1},
            ).returning(AuthRateLimitBucket.count)
            count = int(db.execute(statement).scalar_one())
            if epoch % 97 == 0:
                db.execute(delete(AuthRateLimitBucket).where(AuthRateLimitBucket.window_ends_at < now))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Authentication rate-limit storage failed")
            return None
        finally:
            db.close()
        if count <= limit:
            return None
        return max(1, int((window_end - now).total_seconds()))

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path in {
            "/api/auth/login",
            "/api/auth/register",
        }:
            kind = "login" if request.url.path.endswith("/login") else "register"
            if kind == "register" and get_settings().registration_mode == "disabled":
                return JSONResponse(
                    {"detail": "Public registration is disabled; ask an administrator for an account"},
                    status_code=403,
                )
            retry_after = await run_in_threadpool(self._limited, kind, client_ip(request))
            if retry_after is not None:
                return JSONResponse(
                    {"detail": "Too many authentication requests"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
        return await call_next(request)
