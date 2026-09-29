from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import RagIndexState
from app.rag.embedding import embed_texts
from app.rag.retriever import RetrievedChunk, iter_markdown_chunks


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


def qdrant_distance() -> object:
    from qdrant_client import models

    settings = get_settings()
    normalized = settings.qdrant_distance.strip().upper()
    try:
        return getattr(models.Distance, normalized)
    except AttributeError:
        return models.Distance.COSINE


def create_collection(client, collection_name: str, vector_size: int) -> None:
    from qdrant_client import models

    distance = qdrant_distance()
    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(size=vector_size, distance=distance),
    )


def upsert_chunks(client, collection_name: str, chunks: list[RetrievedChunk], vectors: list[list[float]]) -> None:
    from qdrant_client import models

    points = [
        models.PointStruct(id=index, vector=vector, payload=chunk_payload(chunk, index))
        for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    client.upsert(collection_name=collection_name, points=points)


def _collection_names(client) -> set[str]:
    return {item.name for item in client.get_collections().collections}


def _aliases(client) -> dict[str, str]:
    return {item.alias_name: item.collection_name for item in client.get_aliases().aliases}


def promote_collection(
    client,
    alias_name: str,
    new_collection: str,
    *,
    vector_size: int,
    chunks: list[RetrievedChunk],
    vectors: list[list[float]],
) -> str | None:
    """Promote a fully populated collection, preserving a working fallback."""

    from qdrant_client import models

    aliases = _aliases(client)
    previous = aliases.get(alias_name)
    if previous is not None:
        client.update_collection_aliases(
            change_aliases_operations=[
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=alias_name)
                ),
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=new_collection,
                        alias_name=alias_name,
                    )
                ),
            ]
        )
        return previous

    collections = _collection_names(client)
    if alias_name not in collections:
        client.update_collection_aliases(
            change_aliases_operations=[
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=new_collection,
                        alias_name=alias_name,
                    )
                )
            ]
        )
        return None

    # One-time migration from the old direct collection to an alias.  The new
    # collection is already complete; if alias creation fails, restore the old
    # public collection from the in-memory build before surfacing the error.
    client.delete_collection(collection_name=alias_name)
    try:
        client.update_collection_aliases(
            change_aliases_operations=[
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=new_collection,
                        alias_name=alias_name,
                    )
                )
            ]
        )
    except Exception:
        create_collection(client, alias_name, vector_size)
        upsert_chunks(client, alias_name, chunks, vectors)
        raise
    return None


def prune_old_builds(client, alias_name: str, keep: int) -> None:
    active = _aliases(client).get(alias_name)
    prefix = f"{alias_name}__build_"
    builds = sorted(
        (name for name in _collection_names(client) if name.startswith(prefix)),
        reverse=True,
    )
    retained = 0
    for name in builds:
        if name == active or retained < keep:
            retained += 1
            continue
        client.delete_collection(collection_name=name)


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
    build_stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    build_collection = (
        f"{settings.qdrant_collection}__build_{build_stamp}_{knowledge_hash(root)[:8]}"
    )
    create_collection(client, build_collection, vector_size)
    try:
        upsert_chunks(client, build_collection, chunks, vectors)
        info = client.get_collection(build_collection)
        if int(getattr(info, "points_count", 0) or 0) != len(chunks):
            raise RuntimeError("Qdrant verification count does not match chunk count")
        promote_collection(
            client,
            settings.qdrant_collection,
            build_collection,
            vector_size=vector_size,
            chunks=chunks,
            vectors=vectors,
        )
    except Exception:
        if build_collection != _aliases(client).get(settings.qdrant_collection):
            try:
                client.delete_collection(collection_name=build_collection)
            except Exception:
                pass
        raise
    prune_old_builds(
        client,
        settings.qdrant_collection,
        settings.qdrant_rebuild_keep_collections,
    )

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
