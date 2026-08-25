import shutil
from pathlib import Path

import httpx

from app.auth.passwords import password_backend_status
from app.config import Settings, get_settings


async def check_http(name: str, url: str, api_key: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url, headers=headers)
        status = "ok" if response.status_code < 500 else "warn"
        return {"name": name, "status": status, "detail": f"HTTP {response.status_code}"}
    except (httpx.HTTPError, OSError) as exc:
        return {"name": name, "status": "warn", "detail": str(exc)}


async def check_sse_http(name: str, url: str) -> dict:
    try:
        async with (
            httpx.AsyncClient(timeout=8) as client,
            client.stream("GET", url) as response,
        ):
            status = "ok" if response.status_code < 500 else "warn"
            return {"name": name, "status": status, "detail": f"HTTP {response.status_code}"}
    except (httpx.HTTPError, OSError) as exc:
        return {"name": name, "status": "warn", "detail": str(exc)}


def check_path(name: str, path: Path | None, executable: bool = False) -> dict:
    if path is None:
        return {"name": name, "status": "warn", "detail": "not configured"}
    if not path.exists():
        return {"name": name, "status": "fail", "detail": f"missing: {path}"}
    if executable and not shutil.which(str(path)):
        return {"name": name, "status": "warn", "detail": f"exists but not on PATH: {path}"}
    return {"name": name, "status": "ok", "detail": str(path)}


def check_model_file(settings: Settings, name: str, relative_path: str) -> dict:
    root = settings.pskit_model_parameters
    if root is None:
        return {"name": name, "status": "warn", "detail": "PSKIT_MODEL_PARAMETERS not configured"}
    path = root / relative_path
    if path.exists():
        return {"name": name, "status": "ok", "detail": str(path)}
    return {"name": name, "status": "fail", "detail": f"missing: {path}"}


async def run_doctor() -> dict:
    settings = get_settings()
    checks = [
        password_backend_status(),
        check_path("PSKIT_MODEL_PARAMETERS", settings.pskit_model_parameters),
        check_model_file(settings, "INABe_RNA.pth", "INABe_RNA.pth"),
        check_model_file(settings, "INABe_DNA.pth", "INABe_DNA.pth"),
        check_model_file(settings, "ESM2 650M", "esm2_650M"),
        check_model_file(settings, "SaProt 650M", "SaProt_650M_PDB"),
        check_model_file(settings, "pair_pskit.pt", "pair_pskit.pt"),
        check_model_file(settings, "ESM2 150M", "esm2_150M"),
        check_model_file(settings, "RNA-FM", "rna-fm/RNA-FM_pretrained.pth"),
        check_path("Foldseek", settings.pskit_foldseek),
        check_path("DSSP / mkdssp", settings.pskit_dssp),
        check_path("AF3 database", settings.pskit_af3_db_dir),
        check_path("AF3 model parameters", settings.pskit_af3_model_dir),
    ]
    checks.extend(
        [
            await check_http("LLM API", settings.llm_base_url),
            await check_http("Embedding API", settings.embedding_base_url),
            await check_http("Qdrant", settings.qdrant_url),
        ]
    )
    if settings.coral_mcp_sse_url:
        checks.append(await check_sse_http("CORAL MCP", settings.coral_mcp_sse_url))
    if settings.pepccd_mcp_sse_url:
        checks.append(await check_sse_http("PepCCD MCP", settings.pepccd_mcp_sse_url))
    fail_count = sum(1 for item in checks if item["status"] == "fail")
    warn_count = sum(1 for item in checks if item["status"] == "warn")
    overall = "fail" if fail_count else "warn" if warn_count else "ok"
    return {
        "overall": overall,
        "fail_count": fail_count,
        "warn_count": warn_count,
        "checks": checks,
    }
