# ADR 0002: Use Qdrant Over Chroma For PSKit 2.0

## Status

Accepted.

## Context

The current PSKit uses Chroma successfully for Markdown knowledge RAG. However, PSKit 2.0 aims to be a production-oriented portfolio project.

## Decision

Use Qdrant as the primary vector database.

## Rationale

Qdrant gives a stronger production story:

- Dedicated vector search engine.
- Payload metadata filters.
- Payload indexes.
- Hybrid retrieval support.
- Better fit for multi-user and category-filtered knowledge retrieval.

## Migration

Current Chroma documents should be re-indexed from Markdown source files rather than exported from Chroma. The Markdown knowledge files remain the source of truth.

