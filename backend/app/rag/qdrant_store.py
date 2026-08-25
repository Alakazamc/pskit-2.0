from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from qdrant_client.models import Distance
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import RagIndexState
from app.rag.embedding import embed_texts
from app.rag.retriever import RetrievedChunk, iter_markdown_chunks

logger = logging.getLogger(__name__)


def get_qdrant_client():
    try:
        from qdrant_client import QdrantClient
    except Exception as exc:
        raise RuntimeError(f"qdrant-client is not installed: {exc}") from exc
    settings = get_settings()
    if settings.qdrant_path:
        path = settings.qdrant_path
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(path))
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)


def knowledge_root() -> Path:
    settings = get_settings()
    root = settings.knowledge_dir
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    return root


def knowledge_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.glob("*.md")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def chunk_payload(chunk: RetrievedChunk, index: int) -> dict:
    return {
        "source": chunk.source,
        "heading": chunk.heading,
        "content": chunk.content,
        "chunk_index": index,
    }


def qdrant_distance() -> Distance:
    from qdrant_client import models

    settings = get_settings()
    normalized = settings.qdrant_distance.strip().upper()
    try:
        return getattr(models.Distance, normalized)
    except AttributeError:
        return models.Distance.COSINE


def recreate_collection(client, collection_name: str, vector_size: int) -> None:
    from qdrant_client import models

    distance = qdrant_distance()
    if hasattr(client, "recreate_collection"):
        client.recreate_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=vector_size, distance=distance),
        )
        return
    try:
        client.delete_collection(collection_name=collection_name)
    except Exception as exc:
        logger.info("Qdrant collection did not exist before recreation: %s", exc)
    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(size=vector_size, distance=distance),
    )


def upsert_chunks(
    client, collection_name: str, chunks: list[RetrievedChunk], vectors: list[list[float]]
) -> None:
    from qdrant_client import models

    points = [
        models.PointStruct(id=index, vector=vector, payload=chunk_payload(chunk, index))
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    client.upsert(collection_name=collection_name, points=points)


def build_qdrant_index(db: Session) -> dict:
    settings = get_settings()
    root = knowledge_root()
    if not root.exists():
        raise FileNotFoundError(f"Knowledge directory not found: {root}")

    chunks = iter_markdown_chunks(root)
    if not chunks:
        raise RuntimeError(f"No knowledge chunks found under {root}")

    texts = [f"{chunk.heading or ''}\n{chunk.content}" for chunk in chunks]
    vectors = embed_texts(texts)
    if len(vectors) != len(chunks):
        raise RuntimeError("Embedding count does not match chunk count")

    vector_size = len(vectors[0])
    if settings.qdrant_vector_size and settings.qdrant_vector_size != vector_size:
        raise RuntimeError(
            f"Embedding vector size is {vector_size}, but QDRANT_VECTOR_SIZE="
            f"{settings.qdrant_vector_size}"
        )
    client = get_qdrant_client()
    recreate_collection(client, settings.qdrant_collection, vector_size)
    upsert_chunks(client, settings.qdrant_collection, chunks, vectors)

    state = db.get(RagIndexState, settings.qdrant_collection)
    if not state:
        state = RagIndexState(collection=settings.qdrant_collection)
        db.add(state)
    state.knowledge_hash = knowledge_hash(root)
    state.embedding_model = settings.embedding_model
    state.reranker_model = settings.rerank_model
    state.chunk_count = len(chunks)
    db.commit()

    return {
        "collection": settings.qdrant_collection,
        "chunk_count": len(chunks),
        "vector_size": vector_size,
        "knowledge_hash": state.knowledge_hash,
    }
