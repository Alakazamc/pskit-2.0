# Changelog

## 0.3.0 - Production hardening and unified science runtime

- Unified the complete production Agent, research harness, local-model, MCP,
  and AlphaFold3 code paths into the canonical repository and images.
- Added database-backed proxy-aware authentication throttling, bounded sessions,
  transactional task quotas, GPU allowlists, and administrator audit events.
- Added resumable Agent turns, task artifact downloads/retries, and an admin
  user/metrics interface.
- Made RAG rebuilds atomic through versioned Qdrant collections and alias swaps.
- Added the production-schema migration, dependency audit, CI quality gates,
  verified SQLite/artifact/Qdrant backups, health monitoring, log rotation, and
  container resource limits.
- Corrected Compose health paths, startup dependencies, persistent host paths,
  server image overlays, and explicit CORAL/PepCCD configuration.

## 0.2.0 - Delivery hardening

- Closed public registration after first-admin bootstrap by default and added an admin-only user creation endpoint.
- Added login rate limiting, artifact ownership enforcement, task input limits, security headers, and Markdown sanitization.
- Made task claiming atomic and added stale-task recovery with bounded attempts.
- Added database migrations, dependency lock files, real readiness checks, pagination, and finite provider timeouts.
- Added backend queue/security tests, frontend tests and lint configuration, and stronger CI gates.

## [2.0.0] - 2026-06-20

### Added

- 基于 FastAPI、Vue 3 和 LangGraph 重构 PSKit 2.0。
- 增加 Planner、Tool Executor、Synthesizer 多阶段 Agent 工作流。
- 增加 Qdrant + BAAI/bge-m3 项目知识库 RAG。
- 增加登录注册、多用户会话和文件权限隔离。
- 增加长任务队列、artifact 管理和 Markdown 报告生成。
- 接入 AlphaFold3、INABe、PAIR、RCSB PDB、UniProt、RNAcentral 和 remote RNA expert。
- 增加 SSE 工具执行事件和真正的 token 逐字输出。
- 增加 runtime doctor、A6000 启动脚本和部署文档。

### Fixed

- 修复 RCSB PDB 全文搜索请求格式错误。
- 修复 DeepSeek API endpoint 配置问题。
- 修复 Agent 会话文件路径和结果读取问题。
- 修复右侧状态栏横向溢出问题。
