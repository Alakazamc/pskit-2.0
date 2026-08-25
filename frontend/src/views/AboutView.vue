<template>
  <section class="content-panel">
    <h2>PSKit 2.0 系统架构</h2>
    <p>
      2.0 重构保留旧版 PSKit 的科学计算运行时，同时将任务编排、账号认证、
      权限管理、智能体对话和前端界面拆分到更清晰的服务架构中。
    </p>
    <div class="spec-grid">
      <article>
        <h3>后端服务</h3>
        <p>基于 FastAPI、SQLAlchemy 和 SQLite/PostgreSQL-ready 数据模型，实现 HttpOnly Cookie 登录、RBAC 和文件归属校验。</p>
      </article>
      <article>
        <h3>智能体编排</h3>
        <p>采用 LangGraph-oriented 设计，支持 OpenAI-compatible 对话、工具调用、任务事件和 RAG 来源引用。</p>
      </article>
      <article>
        <h3>模型与工具</h3>
        <p>集成 INABe、ESM2、SaProt、RNA-FM、PAIR、AlphaFold3、远程 RNA expert 和结构分析工具。</p>
      </article>
      <article>
        <h3>知识库</h3>
        <p>Markdown 文档通过 BAAI/bge-m3 向量化写入 Qdrant，用于 RAG 检索、能力说明和故障排查。</p>
      </article>
    </div>
  </section>
</template>
