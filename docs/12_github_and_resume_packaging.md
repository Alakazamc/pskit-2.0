# GitHub and Resume Packaging

## GitHub README Positioning

Use this description:

```text
PSKit 2.0 is a LangGraph-powered BioAI Agent platform for protein-nucleic-acid analysis. It combines a Vue 3 frontend, FastAPI backend, PostgreSQL, Qdrant RAG, Celery/Redis long-task orchestration, and A6000 model runtime integration for structure retrieval, binding-site prediction, AlphaFold 3 jobs, and RNA design workflows.
```

## Resume Bullets

```text
Designed PSKit 2.0, a Vue + FastAPI + LangGraph BioAI Agent platform for protein-nucleic-acid analysis, supporting RCSB/UniProt/RNAcentral lookup, binding-site prediction, AlphaFold 3 tasks, remote RNA design, and session report generation.
```

```text
Built a production-oriented RAG architecture with Qdrant, BAAI/bge-m3 embeddings, reranking, source citations, and stale-index checks over PSKit tool documentation, model dependencies, deployment notes, and troubleshooting knowledge.
```

```text
Designed a Celery + Redis long-task system for GPU/model workloads, including AlphaFold 3, INABe, PAIR, feature extraction, report generation, task status streaming, and artifact registration.
```

```text
Implemented a secure multi-user architecture with Argon2id password hashing, HttpOnly cookie sessions, RBAC, and backend ownership checks for Agent sessions, tasks, reports, and downloaded artifacts.
```

```text
Designed a runtime doctor system to validate LLM APIs, embedding APIs, Qdrant, PostgreSQL, Redis, MinIO, model weights, Foldseek, DSSP, AlphaFold 3 resources, and remote MCP services before runtime failures.
```

## Interview Framing

Say:

```text
The project is not just a chatbot. It is a full-stack BioAI workflow system. The Agent uses RAG to understand the platform's real capabilities, LangGraph to orchestrate tools and long-running tasks, and a task/artifact system to make model outputs traceable and downloadable.
```

## What To Avoid Saying

Avoid:

- "I just called several APIs."
- "It is a wrapper around models."
- "RAG is just a prompt."
- "The frontend is just a chat UI."

Say instead:

- "tool orchestration"
- "stateful LangGraph workflow"
- "runtime dependency validation"
- "artifact ownership"
- "RAG citation and stale-index detection"
- "GPU long-task queue"

