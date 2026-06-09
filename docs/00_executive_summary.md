# Executive Summary

## Goal

PSKit 2.0 rebuilds the current PSKit into a more authoritative AI application stack:

```text
Vue 3 + FastAPI + LangGraph + PostgreSQL + Qdrant + BGE-M3 + Celery/Redis
```

The objective is not only to keep the current bioinformatics tools working, but to make the platform easier to explain, maintain, deploy, test, and present on GitHub.

## Product Definition

PSKit 2.0 is a multi-user BioAI Agent platform for protein-nucleic-acid analysis. Users can:

- Search and download structures from RCSB PDB.
- Query UniProt and RNAcentral.
- Split complexes and extract fragments.
- Generate contact maps.
- Annotate protein-nucleic-acid binding pairs.
- Predict DNA/RNA binding sites.
- Predict protein-nucleic-acid sequence interaction.
- Run AlphaFold 3 tasks.
- Generate RNA candidates through a remote MCP RNA expert service.
- Ask the Agent how PSKit works using RAG-backed system knowledge.
- Generate reports from completed sessions.

## Why Rebuild

The current PSKit works, but its backend is Rust-heavy. For an AI application development internship, a Python-centered stack is easier to defend because:

- Most BioAI model code is Python.
- LangGraph is Python-native and suitable for agent workflow orchestration.
- FastAPI is natural for AI service APIs.
- Qdrant, Redis, Celery, MinIO, and PostgreSQL are production-recognized components.

## Key Improvements Over Current Version

- Replace Rust Agent loop with LangGraph state machine.
- Replace Chroma with Qdrant for stronger metadata filtering and production RAG positioning.
- Replace SQLite-first architecture with PostgreSQL-first architecture.
- Add Celery + Redis for long GPU/model tasks.
- Add MinIO/S3-compatible artifact storage.
- Preserve runtime doctor and improve it into a first-class admin capability.
- Make tool failures structured and diagnosable.
- Keep A6000 native deployment, but make local Docker Compose development possible.

## Non-Goals For First Implementation

- Do not retrain BioAI models.
- Do not move model weights into Git.
- Do not implement billing.
- Do not support arbitrary user-uploaded private RAG documents in v1.
- Do not make AlphaFold 3 synchronous in the Agent request path.

