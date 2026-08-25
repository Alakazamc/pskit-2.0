# Knowledge Base

Markdown files in this directory are the source corpus for PSKit's RAG layer.
Qdrant is a rebuildable index, not the source of truth. Keep entries aligned
with the current API, tool catalog, environment template, and deployment model.

After changing the corpus or embedding model, rebuild the vector index. When an
embedding provider is unavailable, PSKit may use keyword retrieval from these
files. Never add API keys, passwords, private infrastructure addresses, user
data, or generated artifacts to this directory.
