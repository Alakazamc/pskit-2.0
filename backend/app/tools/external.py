from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.artifacts.service import register_local_artifact, resolve_owned_local_artifact, session_artifact_dir
from app.config import get_settings
from app.db.models import User


class ToolExecutionError(RuntimeError):
    pass


def normalize_pdb_id(pdb_id: str) -> str:
    value = pdb_id.strip().upper()
    if len(value) != 4 or not value.isalnum():
        raise ToolExecutionError("pdb_id must be a 4-character alphanumeric PDB ID")
    return value


def build_rcsb_full_text_payload(query: str, rows: int = 10) -> dict:
    value = query.strip()
    if not value:
        raise ToolExecutionError("query must not be empty")
    return {
        "query": {
            "type": "terminal",
            "service": "full_text",
            "parameters": {"value": value},
        },
        "request_options": {"paginate": {"start": 0, "rows": rows}},
        "return_type": "entry",
    }


def search_pdb(query: str) -> dict:
    settings = get_settings()
    payload = build_rcsb_full_text_payload(query)
    with httpx.Client(timeout=30) as client:
        response = client.post(settings.rcsb_search_url, json=payload)
    if response.status_code >= 400:
        raise ToolExecutionError(f"RCSB search failed: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    hits = [
        {"pdb_id": item.get("identifier"), "score": item.get("score")}
        for item in data.get("result_set", [])
    ]
    return {
        "query": query.strip(),
        "count": len(hits),
        "total_count": int(data.get("total_count") or len(hits)),
        "hits": hits,
    }


def fetch_pdb_info(pdb_id: str) -> dict:
    settings = get_settings()
    pdb_id = normalize_pdb_id(pdb_id)
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{settings.rcsb_data_base.rstrip('/')}/{pdb_id}")
    if response.status_code >= 400:
        raise ToolExecutionError(f"RCSB entry lookup failed: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    return {
        "pdb_id": pdb_id,
        "title": data.get("struct", {}).get("title"),
        "method": (data.get("exptl") or [{}])[0].get("method"),
        "release_date": data.get("rcsb_accession_info", {}).get("initial_release_date"),
        "polymer_entity_count": data.get("rcsb_entry_info", {}).get("polymer_entity_count"),
        "raw": data,
    }


def download_pdb_file(
    db: Session,
    user: User,
    session_id: UUID,
    tool_call_id: str,
    pdb_id: str,
    file_format: str = "cif",
) -> dict:
    settings = get_settings()
    pdb_id = normalize_pdb_id(pdb_id)
    fmt = file_format.lower().strip() or "cif"
    if fmt not in {"cif", "pdb"}:
        raise ToolExecutionError("format must be cif or pdb")
    suffix = "cif" if fmt == "cif" else "pdb"
    url = f"{settings.rcsb_files_base.rstrip('/')}/{pdb_id}.{suffix}"
    with httpx.Client(timeout=60) as client:
        response = client.get(url)
    if response.status_code >= 400:
        raise ToolExecutionError(f"Download structure failed: HTTP {response.status_code} {response.text[:300]}")
    out_dir = session_artifact_dir(user.id, session_id, tool_call_id)
    path = out_dir / f"{pdb_id.lower()}.{suffix}"
    path.write_bytes(response.content)
    artifact = register_local_artifact(
        db,
        user,
        session_id,
        None,
        path,
        "structure",
        "chemical/x-cif" if fmt == "cif" else "chemical/x-pdb",
    )
    return {
        "pdb_id": pdb_id,
        "format": fmt,
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "path": str(path),
        "download_url": f"/api/files/{artifact.id}/download",
    }


def search_uniprot(query: str) -> dict:
    settings = get_settings()
    params = {"query": query, "format": "json", "size": 10}
    with httpx.Client(timeout=30) as client:
        response = client.get(settings.uniprot_search_url, params=params)
    if response.status_code >= 400:
        raise ToolExecutionError(f"UniProt search failed: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    results = []
    for item in data.get("results", [])[:10]:
        protein = item.get("proteinDescription", {}).get("recommendedName", {}).get("fullName", {})
        organism = item.get("organism", {})
        results.append(
            {
                "accession": item.get("primaryAccession"),
                "entry_name": item.get("uniProtkbId"),
                "protein_name": protein.get("value"),
                "organism": organism.get("scientificName"),
            }
        )
    return {"query": query, "count": len(results), "results": results}


def fetch_uniprot_entry(accession: str) -> dict:
    settings = get_settings()
    accession = accession.strip()
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{settings.uniprot_entry_base.rstrip('/')}/{accession}.json")
    if response.status_code >= 400:
        raise ToolExecutionError(f"UniProt entry lookup failed: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    sequence = data.get("sequence", {})
    return {
        "accession": accession,
        "entry_name": data.get("uniProtkbId"),
        "organism": data.get("organism", {}).get("scientificName"),
        "sequence_length": sequence.get("length"),
        "sequence": sequence.get("value"),
        "raw": data,
    }


def search_rnacentral(query: str) -> dict:
    settings = get_settings()
    params = {"q": query, "format": "json", "page_size": 10}
    with httpx.Client(timeout=30) as client:
        response = client.get(settings.rnacentral_search_url, params=params)
    if response.status_code >= 400:
        raise ToolExecutionError(f"RNAcentral search failed: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    results = []
    for item in data.get("results", [])[:10]:
        results.append(
            {
                "accession": item.get("rnacentral_id") or item.get("upi"),
                "description": item.get("description"),
                "species": item.get("species"),
            }
        )
    return {"query": query, "count": len(results), "results": results}


def fetch_rnacentral_entry(accession: str) -> dict:
    settings = get_settings()
    accession = accession.strip()
    with httpx.Client(timeout=30) as client:
        response = client.get(f"{settings.rnacentral_entry_base.rstrip('/')}/{accession}", params={"format": "json"})
    if response.status_code >= 400:
        raise ToolExecutionError(f"RNAcentral entry lookup failed: HTTP {response.status_code} {response.text[:300]}")
    return response.json()


def serpapi_search(query: str, num: int = 5, engine: str = "google") -> dict:
    settings = get_settings()
    if not settings.serpapi_api_key:
        raise ToolExecutionError("SERPAPI_API_KEY is not configured")
    params = {
        "engine": engine or "google",
        "q": query,
        "num": max(1, min(int(num or 5), 10)),
        "api_key": settings.serpapi_api_key,
    }
    with httpx.Client(timeout=30) as client:
        response = client.get(settings.serpapi_search_url, params=params)
    if response.status_code >= 400:
        raise ToolExecutionError(f"SerpAPI search failed: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    results = [
        {
            "title": item.get("title"),
            "link": item.get("link"),
            "snippet": item.get("snippet"),
        }
        for item in data.get("organic_results", [])[: params["num"]]
    ]
    return {"query": query, "count": len(results), "results": results}


def read_result_file(
    db: Session,
    user: User,
    artifact_id: str | None = None,
    file_path: str | None = None,
    max_chars: int = 8000,
) -> dict:
    if artifact_id:
        path = resolve_owned_local_artifact(db, user, artifact_id)
    elif file_path:
        settings = get_settings()
        root = settings.artifact_dir.resolve()
        path = Path(file_path).resolve()
        if root not in path.parents and path != root:
            raise ToolExecutionError("file_path must be under artifact root")
        if not path.exists() or not path.is_file():
            raise ToolExecutionError("file_path does not exist or is not a file")
    else:
        raise ToolExecutionError("artifact_id or file_path is required")

    text = path.read_text(encoding="utf-8", errors="replace")
    limit = max(100, min(int(max_chars or 8000), 50000))
    return {
        "file": str(path),
        "returned_chars": min(len(text), limit),
        "total_chars": len(text),
        "truncated": len(text) > limit,
        "content": text[:limit],
    }


def json_dumps_result(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
