# Changelog

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

### Deployment

- PSKit 2.0 部署至 A6000。
- 当前访问地址：`http://127.0.0.1:10716/agent`
- GitHub：`https://github.com/Alakazamc/pskit-2.0`
