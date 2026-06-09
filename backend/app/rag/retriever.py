from dataclasses import dataclass
from pathlib import Path
import re

from app.config import get_settings
from app.rag.embedding import embed_texts


TOKEN_RE = re.compile(r"[A-Za-z0-9_\-./]+|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class RetrievedChunk:
    source: str
    heading: str | None
    content: str
    score: float


def tokenize(text: str) -> set[str]:
    return {item.lower() for item in TOKEN_RE.findall(text)}


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
        score = len(overlap) + heading_boost
        scored.append(
            RetrievedChunk(
                source=chunk.source,
                heading=chunk.heading,
                content=chunk.content[:1600],
                score=float(score),
            )
        )
    return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]


def retrieve_qdrant(query: str, top_k: int = 5) -> list[RetrievedChunk]:
    settings = get_settings()
    try:
        from qdrant_client import QdrantClient
    except Exception as exc:
        raise RuntimeError(f"qdrant-client is not installed: {exc}") from exc

    vector = embed_texts([query])[0]
    client = QdrantClient(url=settings.qdrant_url)

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
        chunks = retrieve_qdrant(query, top_k=top_k)
        if chunks:
            return "qdrant", chunks
    except Exception:
        pass
    return "keyword_fallback", retrieve_keyword(query, top_k=top_k)
