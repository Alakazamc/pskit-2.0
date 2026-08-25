from dataclasses import dataclass
from pathlib import Path
import re

from app.config import get_settings
from app.rag.embedding import embed_texts


TOKEN_RE = re.compile(r"[A-Za-z0-9_\-./]+|[\u4e00-\u9fff]+")
AUTHORITATIVE_SOURCE = "00_current_pskit2_capabilities.md"


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    heading: str | None
    content: str
    score: float


def tokenize(text: str) -> set[str]:
    return {item.lower() for item in TOKEN_RE.findall(text)}


def prioritize_authoritative(
    chunks: list[RetrievedChunk],
    top_k: int,
) -> list[RetrievedChunk]:
    authoritative = next(
        (chunk for chunk in chunks if chunk.source == AUTHORITATIVE_SOURCE),
        None,
    )
    remaining: list[RetrievedChunk] = []
    seen: set[tuple[str, str | None, str]] = set()
    if authoritative:
        seen.add((authoritative.source, authoritative.heading, authoritative.content))
    for chunk in chunks:
        key = (chunk.source, chunk.heading, chunk.content)
        if key in seen:
            continue
        seen.add(key)
        remaining.append(chunk)
    ordered = ([authoritative] if authoritative else []) + remaining
    return ordered[:top_k]


def iter_markdown_chunks(knowledge_dir: Path) -> list[RetrievedChunk]:
    chunks: list[RetrievedChunk] = []
    for path in sorted(knowledge_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        heading: str | None = None
        buffer: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("#"):
                if buffer:
                    chunks.append(
                        RetrievedChunk(
                            source=path.name,
                            heading=heading,
                            content="\n".join(buffer).strip(),
                            score=0,
                        )
                    )
                    buffer = []
                heading = line.strip("# ").strip()
            else:
                buffer.append(line)
        if buffer:
            chunks.append(
                RetrievedChunk(
                    source=path.name,
                    heading=heading,
                    content="\n".join(buffer).strip(),
                    score=0,
                )
            )
    return [chunk for chunk in chunks if chunk.content]


def retrieve_keyword(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    settings = get_settings()
    knowledge_dir = settings.knowledge_dir
    if not knowledge_dir.is_absolute():
        knowledge_dir = (Path.cwd() / knowledge_dir).resolve()
    if not knowledge_dir.exists():
        return []

    query_tokens = tokenize(query)
    scored: list[RetrievedChunk] = []
    for chunk in iter_markdown_chunks(knowledge_dir):
        chunk_tokens = tokenize(f"{chunk.heading or ''}\n{chunk.content}")
        overlap = query_tokens & chunk_tokens
        if not overlap:
            continue
        heading_boost = 0.5 if chunk.heading and tokenize(chunk.heading) & query_tokens else 0.0
        authoritative_boost = 2.0 if chunk.source == AUTHORITATIVE_SOURCE else 0.0
        score = len(overlap) + heading_boost + authoritative_boost
        scored.append(
            RetrievedChunk(
                source=chunk.source,
                heading=chunk.heading,
                content=chunk.content[:1600],
                score=float(score),
            )
        )
    return prioritize_authoritative(
        sorted(scored, key=lambda item: item.score, reverse=True),
        top_k,
    )


def retrieve_qdrant(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    settings = get_settings()
    from app.rag.qdrant_store import get_qdrant_client

    vector = embed_texts([query])[0]
    client = get_qdrant_client()

    if hasattr(client, "query_points"):
        response = client.query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
        points = response.points
    else:
        points = client.search(
            collection_name=settings.qdrant_collection,
            query_vector=vector,
            limit=top_k,
            with_payload=True,
        )

    chunks: list[RetrievedChunk] = []
    for point in points:
        payload = point.payload or {}
        chunks.append(
            RetrievedChunk(
                source=str(payload.get("source") or "qdrant"),
                heading=payload.get("heading"),
                content=str(payload.get("content") or "")[:1600],
                score=float(point.score or 0.0),
            )
        )
    return chunks


def retrieve(query: str, top_k: int = 5) -> tuple[str, list[RetrievedChunk]]:
    try:
        chunks = retrieve_qdrant(query, top_k=max(top_k * 2, top_k))
        if chunks:
            settings = get_settings()
            knowledge_dir = settings.knowledge_dir
            if not knowledge_dir.is_absolute():
                knowledge_dir = (Path.cwd() / knowledge_dir).resolve()
            current_chunks = [
                chunk
                for chunk in iter_markdown_chunks(knowledge_dir)
                if chunk.source == AUTHORITATIVE_SOURCE
            ]
            return "qdrant", prioritize_authoritative([*current_chunks[:1], *chunks], top_k)
    except Exception:
        pass
    return "keyword_fallback", retrieve_keyword(query, top_k=top_k)
