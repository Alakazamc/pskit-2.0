from __future__ import annotations

import httpx

from app.config import get_settings


class EmbeddingUnavailable(Exception):
    pass


def embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not settings.embedding_api_key:
        raise EmbeddingUnavailable("EMBEDDING_API_KEY is not configured")
    if not texts:
        return []

    payload = {
        "model": settings.embedding_model,
        "input": texts,
    }
    headers = {"Authorization": f"Bearer {settings.embedding_api_key}"}
    with httpx.Client(timeout=60) as client:
        response = client.post(settings.embeddings_url, headers=headers, json=payload)
    if response.status_code >= 400:
        raise EmbeddingUnavailable(
            f"Embedding API returned {response.status_code}: {response.text[:500]}"
        )

    body = response.json()
    data = body.get("data")
    if not isinstance(data, list):
        raise EmbeddingUnavailable("Embedding API response missing data list")
    vectors = []
    for item in data:
        vector = item.get("embedding") if isinstance(item, dict) else None
        if not isinstance(vector, list):
            raise EmbeddingUnavailable("Embedding API response contains invalid vector")
        vectors.append([float(value) for value in vector])
    return vectors
