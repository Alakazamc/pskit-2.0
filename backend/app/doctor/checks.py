import asyncio
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

from app.auth.passwords import password_backend_status
from app.config import Settings, get_settings
from app.db.models import ServiceHeartbeat, ensure_utc, now_utc
from app.tools.mcp_adapters import (
    MCPAdapterError,
    safe_mcp_exception_message,
    select_pepccd_mcp_tool_name,
)


async def check_http(name: str, url: str, api_key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url, headers=headers)
        status = "ok" if 200 <= response.status_code < 400 else "warn"
        return {"name": name, "status": status, "detail": f"HTTP {response.status_code}"}
    except Exception as exc:
        return {
            "name": name,
            "status": "warn",
            "detail": f"request failed ({exc.__class__.__name__})",
        }


async def check_sse_endpoint(name: str, url: str) -> dict:
    if not url:
        return {"name": name, "status": "warn", "detail": "not configured"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            async with client.stream(
                "GET",
                url,
                headers={"Accept": "text/event-stream"},
            ) as response:
                status = "ok" if 200 <= response.status_code < 300 else "warn"
                content_type = response.headers.get("content-type", "unknown")
                return {
                    "name": name,
                    "status": status,
                    "detail": f"HTTP {response.status_code}; {content_type}",
                }
    except Exception as exc:
        return {
            "name": name,
            "status": "warn",
            "detail": f"request failed ({exc.__class__.__name__})",
        }


async def check_sse_mcp_endpoint(name: str, url: str) -> dict:
    if not url:
        return {"name": name, "status": "warn", "detail": "not configured"}
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except Exception as exc:
        return {
            "name": name,
            "status": "fail",
            "detail": f"MCP SDK unavailable ({exc.__class__.__name__})",
        }

    async def inspect_tool() -> str:
        async with sse_client(url) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                for tool in tools.tools:
                    if getattr(tool, "name", "") != "generate_rna_for_protein":
                        continue
                    schema = getattr(tool, "inputSchema", None) or {}
                    properties = schema.get("properties") if isinstance(schema, dict) else {}
                    required = {"pdb_id", "chain", "num_samples"}
                    if not isinstance(properties, dict) or not required.issubset(properties):
                        raise MCPAdapterError(
                            "CORAL MCP tool schema is missing pdb_id, chain, or num_samples"
                        )
                    return "generate_rna_for_protein"
                raise MCPAdapterError("CORAL MCP tool generate_rna_for_protein is unavailable")

    try:
        tool_name = await asyncio.wait_for(inspect_tool(), timeout=8)
        return {"name": name, "status": "ok", "detail": f"tool: {tool_name}"}
    except (MCPAdapterError, asyncio.TimeoutError) as exc:
        return {"name": name, "status": "warn", "detail": str(exc)}
    except Exception as exc:
        return {
            "name": name,
            "status": "warn",
            "detail": safe_mcp_exception_message(name, exc),
        }


async def check_streamable_mcp_endpoint(
    name: str,
    url: str,
    *,
    configured_tool_name: str | None,
) -> dict:
    if not url:
        return {"name": name, "status": "warn", "detail": "not configured"}
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
    except Exception as exc:
        return {
            "name": name,
            "status": "fail",
            "detail": f"MCP SDK unavailable ({exc.__class__.__name__})",
        }
    async def inspect_tool() -> str:
        async with streamable_http_client(url) as streams:
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                return select_pepccd_mcp_tool_name(
                    tools.tools,
                    configured_name=configured_tool_name,
                )

    try:
        # wzf：服务器使用 Python 3.10，不能使用 Python 3.11 才提供的 asyncio.timeout。
        tool_name = await asyncio.wait_for(inspect_tool(), timeout=8)
        return {"name": name, "status": "ok", "detail": f"tool: {tool_name}"}
    except (MCPAdapterError, asyncio.TimeoutError) as exc:
        return {"name": name, "status": "warn", "detail": str(exc)}
    except Exception as exc:
        return {
            "name": name,
            "status": "warn",
            "detail": safe_mcp_exception_message(name, exc),
        }


async def check_llm_api(settings: Settings) -> dict:
    if not settings.llm_api_key:
        return {"name": "LLM API", "status": "fail", "detail": "API key not configured"}

    def request_completion() -> dict:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                settings.chat_completions_url,
                headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                json={
                    "model": settings.llm_model_id,
                    "messages": [{"role": "user", "content": "Reply exactly OK"}],
                    "max_tokens": 2,
                    "stream": False,
                },
            )
        if not 200 <= response.status_code < 300:
            return {
                "name": "LLM API",
                "status": "fail",
                "detail": f"HTTP {response.status_code}",
            }
        payload = response.json()
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices:
            return {
                "name": "LLM API",
                "status": "fail",
                "detail": "invalid completion response",
            }
        return {
            "name": "LLM API",
            "status": "ok",
            "detail": "authenticated completion succeeded",
        }

    try:
        # wzf：Agent 生产调用使用同步 httpx.Client；Doctor 复用同一传输方式，
        # 放入工作线程以避免代理对异步连接的差异行为阻塞 FastAPI 事件循环。
        return await asyncio.to_thread(request_completion)
    except Exception as exc:
        return {
            "name": "LLM API",
            "status": "fail",
            "detail": f"request failed ({exc.__class__.__name__})",
        }


async def check_embedding_api(settings: Settings) -> dict:
    if not settings.embedding_api_key:
        return {
            "name": "Embedding API",
            "status": "fail",
            "detail": "API key not configured",
        }

    def request_embedding() -> dict:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                settings.embeddings_url,
                headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
                json={
                    "model": settings.embedding_model,
                    "input": ["PSKit health check"],
                },
            )
        if not 200 <= response.status_code < 300:
            return {
                "name": "Embedding API",
                "status": "fail",
                "detail": f"HTTP {response.status_code}",
            }
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            return {
                "name": "Embedding API",
                "status": "fail",
                "detail": "invalid embedding response",
            }
        return {
            "name": "Embedding API",
            "status": "ok",
            "detail": "authenticated embedding succeeded",
        }

    try:
        return await asyncio.to_thread(request_embedding)
    except Exception as exc:
        return {
            "name": "Embedding API",
            "status": "fail",
            "detail": f"request failed ({exc.__class__.__name__})",
        }


# wzf：模型、数据库、二进制和 Docker Socket 只挂载给 science-worker。
# Web 侧 Doctor 必须读取 Worker 已持久化的探针，不能以自身文件视图制造缺失误报。
def science_worker_checks(db: Session, settings: Settings) -> list[dict]:
    heartbeat = db.get(ServiceHeartbeat, "task-worker")
    if heartbeat is None:
        return [
            {
                "name": "Science worker heartbeat",
                "status": "fail",
                "detail": "heartbeat missing",
            },
            {
                "name": "Science worker dependencies",
                "status": "fail",
                "detail": "worker evidence unavailable",
            },
        ]

    heartbeat_time = ensure_utc(heartbeat.updated_at)
    heartbeat_fresh = heartbeat_time >= now_utc() - timedelta(
        seconds=settings.worker_readiness_max_age_seconds
    )
    checks = [
        {
            "name": "Science worker heartbeat",
            "status": "ok" if heartbeat_fresh else "fail",
            "detail": "heartbeat fresh" if heartbeat_fresh else "heartbeat stale",
        }
    ]

    metadata = heartbeat.metadata_json or {}
    probe_status = str(metadata.get("science_probe_status") or "missing")
    raw_checked_at = metadata.get("science_probe_checked_at")
    try:
        checked_at = ensure_utc(
            datetime.fromisoformat(str(raw_checked_at).replace("Z", "+00:00"))
        )
        probe_age = max((now_utc() - checked_at).total_seconds(), 0.0)
    except (TypeError, ValueError):
        probe_age = None
    probe_fresh = (
        probe_status in {"fresh", "refreshing"}
        and isinstance(probe_age, (int, float))
        and float(probe_age) <= settings.science_readiness_max_probe_age_seconds
    )
    missing = metadata.get("missing_dependencies")
    missing_names = (
        ", ".join(str(item) for item in missing)
        if isinstance(missing, list) and missing
        else "worker evidence unavailable"
    )
    science_ready = bool(metadata.get("science_ready")) and probe_fresh
    checks.append(
        {
            "name": "Science worker dependencies",
            "status": "ok" if science_ready else "fail",
            "detail": (
                "model, database, binary and container boundary ready"
                if science_ready
                else (
                    missing_names
                    if probe_fresh
                    else f"dependency probe {probe_status}; stale or unavailable"
                )
            ),
        }
    )
    for item in metadata.get("docker_checks") or []:
        if not isinstance(item, dict):
            continue
        checks.append(
            {
                "name": str(item.get("name") or "Science container runtime"),
                "status": "ok" if item.get("status") == "ok" else "fail",
                "detail": str(item.get("detail") or "worker probe returned no detail"),
            }
        )
    return checks


async def run_doctor(db: Session) -> dict:
    settings = get_settings()
    checks = [
        password_backend_status(),
        *science_worker_checks(db, settings),
    ]
    # wzf：两个外部模型请求依次执行，避免低并发代理把同时建连误判为服务失败；
    # MCP 与 Qdrant 仍可并行，缩短不涉及外部代理的等待。
    model_checks = [
        await check_llm_api(settings),
        await check_embedding_api(settings),
    ]
    service_checks = await asyncio.gather(
        check_http("Qdrant", settings.qdrant_url, settings.qdrant_api_key),
        check_sse_mcp_endpoint(
            "CORAL RNA MCP",
            settings.remote_rna_expert_sse_url,
        ),
        check_streamable_mcp_endpoint(
            "PepCCD MCP",
            settings.pepccd_mcp_url,
            configured_tool_name=settings.pepccd_mcp_tool_name,
        ),
    )
    checks.extend([*model_checks, *service_checks])
    fail_count = sum(1 for item in checks if item["status"] == "fail")
    warn_count = sum(1 for item in checks if item["status"] == "warn")
    overall = "fail" if fail_count else "warn" if warn_count else "ok"
    return {"overall": overall, "fail_count": fail_count, "warn_count": warn_count, "checks": checks}
