# RAG Design With Qdrant

## Goal

RAG should make the Agent aware of:

- PSKit capabilities.
- Available tools.
- Tool workflows.
- Model dependencies.
- Runtime paths.
- Common failures.
- Basic bioinformatics concepts.

RAG is not a replacement for tool schemas or system prompt. It supplies factual context.

## Knowledge Source

Migrate current Markdown files:

```text
agent_config/knowledge/*.md
```

into:

```text
knowledge/*.md
```

## Indexing Pipeline

```text
Markdown files
-> parse headings
-> split into chunks
-> compute content hash
-> embed with BAAI/bge-m3
-> upsert into Qdrant
-> store collection metadata in PostgreSQL
```

## Qdrant Collection

Collection:

```text
pskit_knowledge
```

Vector:

```text
size: 1024
distance: cosine
```

Payload:

```json
{
  "source": "tool_workflows.md",
  "heading": "Binding Site Prediction",
  "chunk_index": 12,
  "knowledge_hash": "...",
  "category": "tool_workflow",
  "visibility": "public_system"
}
```

## Retrieval Pipeline

```text
user query
-> embedding query with BGE-M3
-> Qdrant top-20 vector search
-> optional metadata filter
-> rerank top-20 to top-5
-> format retrieved context
-> inject into LangGraph state
-> stream source citations to frontend
```

## Fallback

If embedding or Qdrant fails:

```text
BM25 / keyword fallback
```

Fallback must also return sources, so frontend can still show citations.

## Stale Index

Compute:

```text
sha256(all markdown file paths + contents)
```

Compare:

- current hash from filesystem.
- indexed hash from PostgreSQL/Qdrant metadata.

Doctor result:

```text
OK: index fresh
WARN: stale index
FAIL: Qdrant unavailable
```

## Citation Format

Every Agent answer that uses retrieved knowledge should include source metadata:

```json
{
  "source": "models_env_and_limits.md",
  "heading": "AlphaFold 3 Runtime",
  "score": 0.82
}
```

Frontend should display source chips near the assistant message.

## Interview Explanation

Use this explanation:

```text
I migrated PSKit's tool documentation, model dependency notes, deployment notes, and troubleshooting guides into a Markdown knowledge base. The indexing pipeline chunks these files by headings, embeds each chunk with BAAI/bge-m3, and stores 1024-dimensional vectors plus metadata in Qdrant. During each Agent turn, LangGraph retrieves top-k relevant chunks, reranks them, injects them into the Agent state, and streams citations to the frontend. This reduces hallucination about available tools and runtime constraints.
```

