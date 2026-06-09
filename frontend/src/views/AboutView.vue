<template>
  <section class="content-panel">
    <h2>PSKit 2.0 architecture</h2>
    <p>
      The 2.0 rebuild keeps the old PSKit scientific runtime but moves orchestration,
      authentication, task management, and the agent interface into a cleaner service
      architecture.
    </p>
    <div class="spec-grid">
      <article>
        <h3>Backend</h3>
        <p>FastAPI, SQLAlchemy, SQLite/PostgreSQL-ready models, HttpOnly cookie auth, RBAC, and ownership checks.</p>
      </article>
      <article>
        <h3>Agent</h3>
        <p>LangGraph-oriented design with OpenAI-compatible chat, tool calls, task events, and RAG source citation.</p>
      </article>
      <article>
        <h3>Models</h3>
        <p>INABe, ESM2, SaProt, RNA-FM, PAIR, AlphaFold3, remote RNA expert, and structure analysis utilities.</p>
      </article>
      <article>
        <h3>Knowledge</h3>
        <p>Markdown knowledge files are designed for Qdrant vector retrieval with BAAI/bge-m3 embeddings and reranking.</p>
      </article>
    </div>
  </section>
</template>
