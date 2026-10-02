# PSKit 2.0 技术与界面核查（2026-09-30）

## 范围与判断方法

本记录按仓库当前源码、依赖清单和 Compose 配置判断**已实现的系统**；设计文档仅用于发现目标与实现的差异。外部判断只引用技术所有者的官方文档。技术栈部分为静态核查；界面部分实际启动了本地 API 与 Vite，观察首页和登录页，并用只读模拟 API 观察空会话 Agent 页。没有运行科研任务、做性能测试或评估检索质量。

## 当前系统地图

| 子系统 | 当前实现 | 入口与边界 |
| --- | --- | --- |
| 前端 | Vue 3、TypeScript、Vite；公开介绍页、受保护的 Agent、任务、工具和管理页 | [应用外壳](../frontend/src/App.vue#L44-L117)、[路由](../frontend/src/router.ts#L15-L27)、[API 客户端](../frontend/src/lib/api.ts) |
| HTTP API | FastAPI 路由、登录会话、权限与文件下载 | [应用装配](../backend/app/main.py#L41-L94)、[认证](../backend/app/api/auth.py)、[任务 API](../backend/app/api/tasks.py) |
| Agent | LangGraph 四节点条件图；模型、工具、审批与消息流另有项目自建代码 | [主图](../backend/app/agent/orchestrator.py#L1384-L1423)、[执行与恢复](../backend/app/agent/execution.py) |
| 检索 | Markdown 知识库、向量嵌入、Qdrant 查询及本地词项兜底 | [检索器](../backend/app/rag/retriever.py#L81-L166)、[索引](../backend/app/rag/qdrant_store.py)、[知识文件](../knowledge/) |
| 长任务 | SQL 表排队、独立 Python Worker 领取与执行、产物登记 | [入队](../backend/app/tasks/service.py#L79-L146)、[领取](../backend/app/tasks/worker.py#L618-L680)、[执行](../backend/app/tasks/worker.py#L2020-L2188) |
| 交付 | 单服务器 Compose：迁移、Web、Worker、Qdrant；默认 SQLite 和本地文件 | [Compose](../compose.yaml)、[Docker 快速部署](../DOCKER_QUICKSTART.md)、[CI](../.github/workflows/ci.yml) |

## 结论

1. **框架版本并不陈旧。** 前端清单锁定 Vue `3.5.35`、Vite `8.0.16`、TypeScript `6.0.3`；后端锁文件给出 FastAPI `0.141.1`、LangGraph `1.2.11`、Qdrant Client `1.19.0`，Compose 使用 Qdrant Server `v1.18.3`。这些是仓库版本事实，不能仅凭版本推断质量或安全状态。[前端清单](../frontend/package.json#L13-L32)、[后端锁文件](../backend/uv.lock#L625-L626)、[LangGraph 锁定](../backend/uv.lock#L1004-L1005)、[Qdrant Client 锁定](../backend/uv.lock#L2287-L2288)、[Compose](../compose.yaml#L95-L102)。Vue 官方文档当前主线仍为 Vue 3；Vite 官方公告记录 Vite 8 于 2026 年发布。[Vue 官方文档](https://vuejs.org/guide/introduction.html)、[Vite 8 官方公告](https://vite.dev/blog/announcing-vite8)。

2. **LangGraph 仍是现行方案，但应按需使用。** LangChain 官方将 LangGraph 定位为长期运行、有状态 Agent 的低层编排运行时；对常见模型工具循环，官方建议先考虑更高层的 `create_agent`。[LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview)、[LangChain 概览](https://docs.langchain.com/oss/python/langchain/overview)。PSKit 的主 Agent 图确实含“检索 → 规划 → 执行工具 → 综合回答”四节点和条件回路，并通过 `graph.stream` 产出节点更新，不是只挂了一个空依赖。[主图定义](../backend/app/agent/orchestrator.py#L1384-L1423)、[运行入口](../backend/app/agent/orchestrator.py#L1489-L1507)。

3. **项目没有使用 LangGraph 的持久化能力。** 主图调用 `graph.compile()` 时未配置 checkpointer/store；每轮重新构建 `initial_state`，再由项目自己的 `AgentTurn` 数据库状态、租约和消息表保存执行状态和结果。[图编译](../backend/app/agent/orchestrator.py#L1404-L1423)、[初始状态](../backend/app/agent/orchestrator.py#L1426-L1457)、[数据库领取与租约](../backend/app/agent/execution.py#L595-L655)、[结果持久化](../backend/app/agent/execution.py#L688-L725)。官方说明跨图运行恢复、线程级记忆和人工中断续跑依赖 checkpointer/store。[LangGraph 持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence)。因此，当前 LangGraph 的直接价值主要是显式路由与节点级流式更新；不能把项目自建的任务恢复归功于 LangGraph checkpoint。另有一个单节点 `app/agent/graph.py`，在生产代码中未见调用，可能是早期遗留，应核实用途再清理。[单节点图](../backend/app/agent/graph.py#L29-L39)、[实际运行入口](../backend/app/agent/execution.py#L647-L657)。

4. **运行架构比旧设计文档轻，也有表述漂移。** 目前任务使用数据库表作为队列，由本地 Python Worker 原子领取、维护租约和心跳，默认空闲轮询间隔 2 秒；Compose 的 `web`、`worker` 和 `qdrant` 是独立服务，数据库默认 SQLite，未定义 Redis/Celery 服务。[任务创建](../backend/app/tasks/service.py#L79-L146)、[领取与租约](../backend/app/tasks/worker.py#L618-L680)、[轮询](../backend/app/tasks/worker.py#L2580-L2613)、[Compose 默认数据库及服务](../compose.yaml#L15-L15)、[Compose 服务](../compose.yaml#L43-L103)。相比之下，已接受的旧 ADR 把 PostgreSQL、Celery/Redis 和 MinIO/S3 列为最终技术栈；较新的技术栈说明则把它们列为后续强化目标。[旧 ADR](adr/0001-final-stack.md#L7-L21)、[实现与目标区分](02_target_technology_stack.md#L7-L45)。评估和对外介绍应以实际代码及最新运维文档为准。

5. **RAG 有功能落差，优先于更换框架。** 实际查询是单路稠密向量 `query_points`（或兼容旧客户端的 `search`）；失效时降级到本地 Markdown 的词项交集计分。`rerank_model` 只在配置和索引元数据里出现，检索流程没有调用重排模型；当前兜底也不是 BM25。[向量检索及降级](../backend/app/rag/retriever.py#L81-L166)、[reranker 配置](../backend/app/config.py#L82-L83)、[索引元数据](../backend/app/rag/qdrant_store.py#L229-L237)。Qdrant 官方提供混合检索、多阶段查询与重排方案，但是否引入须用本项目查询集测质量、延迟和成本。[Qdrant 混合查询](https://qdrant.tech/documentation/search/hybrid-queries/)、[Qdrant 混合检索与重排教程](https://qdrant.tech/documentation/tutorials-basics/reranking-hybrid-search/)。

6. **工程基础并非空白。** CI 已覆盖后端编译、局部 mypy、Ruff、pytest、前端构建与 lint、Vitest、Playwright、依赖审计和容器启动冒烟；这是保留现有能力、逐步改造的有利条件。[CI 流程](../.github/workflows/ci.yml#L1-L95)。同时，主 Agent 编排文件约 1600 行、Worker 文件约 2600 行，状态与策略逻辑集中，后续改动应先界定模块职责与回归场景。[Agent 编排](../backend/app/agent/orchestrator.py)、[Worker](../backend/app/tasks/worker.py)。行数只说明修改面较大，并不单独证明代码质量差。

## 界面与体验核查

观察范围：桌面视口 1440×900、手机视口 390×844；首页和登录页连接本地 API，Agent 页使用只读模拟的用户、会话及空任务响应，因此只评价布局与空状态，不评价真实任务交互。以下定位采用 [Vercel Web Interface Guidelines](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md) 的可访问性和交互规则。

- `frontend/src/router.ts:40` — 首次进入**所有**路由都等待 `/api/auth/me`；`frontend/src/stores/auth.ts:19` 对非 401 错误继续抛出。实测仅启动 Vite、API 不可用时，首页和登录页只显示外壳，主内容空白；API 启动后恢复。公开页面的可用性被后端认证检查绑定。
- `frontend/src/App.vue:46`、`frontend/src/styles.css:54` — 270px 全局侧栏也占据公开首页与登录页。登录页还同时显示顶栏“登录”入口（`frontend/src/App.vue:97`）和表单“登录”按钮；桌面截图中侧栏、顶栏及空白背景比表单更抢眼。`App.vue` 给登录页加的 `compact` 类没有对应 CSS 规则。
- `frontend/src/styles.css:447` — 桌面 Agent 页在全局侧栏以外，再分配 240px 会话栏和约 320px 结果栏；1440px 截图里中央对话区明显被压窄。`frontend/src/views/AgentView.vue:184` 的右栏把工具事件、文件、RAG 来源和任务同时常驻，即使全为空也占用空间。手机端这些空卡片堆在对话区后面，形成较长的无结果页面。
- `frontend/src/styles.css:847` — 窄屏导航改成横向滚动，390px 截图只显示部分入口，隐藏的“任务”“工具”等没有明显滚动提示。优先级高于单纯换配色。
- `frontend/src/views/HomeView.vue:28` — 首页“工具时间线”是写死的示例步骤；画面没有标明“示例”，容易被理解为实时执行状态（基于文案与视觉的推断）。首页和侧栏反复展示框架名与内部工具名，产品目标和用户下一步行动不够集中。[侧栏文案](../frontend/src/App.vue#L72-L76)。
- `frontend/index.html:2` — 页面主体为中文，文档语言却是 `en`，影响读屏器发音与浏览器语言判断。[语言声明规则](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)。
- `frontend/src/views/AgentView.vue:168` — 对话输入 `textarea` 没有可见 `<label>` 或 `aria-label`；提示文字不能充当稳定的可访问名称。[表单可访问性规则](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md)。

视觉判断：青色网格背景、大面积半透明卡片、发光按钮与多处圆角在每个页面重复出现；截图显示层级趋同，科研任务的输入、进度、结果没有获得比装饰性框架信息更高的视觉权重。这是设计评估，不是性能结论。重做时应先调整信息架构与空状态，再统一间距、字体、色彩和组件规则；换成 React 不会自动解决这些问题。

## 建议的先后顺序

1. **先修可见的体验缺口。** 让公开页面在认证 API 不可用时仍能渲染，修正中文 `lang` 和 Agent 输入标签；解决手机导航隐藏、登录页重复入口和无意义空卡片。以同样的桌面与手机场景比较改动前后页面。
2. **重新设计主工作台的信息层级。** 桌面把对话和任务结果作为中心内容，会话列表与诊断信息按使用频率收纳；手机让输入、当前任务与结果先出现。首页明确示例与真实状态，减少面向普通用户的框架名。保持现有 Vue 栈即可完成这一轮。
3. **明确 Agent 状态的唯一事实来源。** 用真实科研查询、工具调用、审批恢复和失败恢复场景判断当前图是否需要跨轮 checkpoint。若保留自建数据库状态机，应明确它与 LangGraph 状态的权责；若采用 LangGraph checkpoint，先设计迁移及恢复边界，避免双份执行真相。[现有图和数据库租约](../backend/app/agent/orchestrator.py#L1404-L1423)、[现有执行流程](../backend/app/agent/execution.py#L595-L725)、[官方持久化语义](https://docs.langchain.com/oss/python/langgraph/persistence)。
4. **以检索评估驱动 RAG 改进。** 先记录命中率、引用正确性、兜底比例和延迟，再决定是否把关键词检索改成真正的稀疏检索并加重排；不要因为已经配置了模型名就对外宣称“已使用 reranker”。[当前检索代码](../backend/app/rag/retriever.py#L81-L166)、[Qdrant 官方方案](https://qdrant.tech/documentation/search/hybrid-queries/)。
5. **修正架构文档。** 将 `docs/adr/0001-final-stack.md`、`docs/03_system_architecture.md` 中的 Celery/Redis、PostgreSQL 和 MinIO/S3 明确标为历史目标；当前实现依据为数据库轮询 Worker、SQLite 默认和本地 artifact 存储。[旧 ADR](adr/0001-final-stack.md#L7-L21)、[当前技术栈说明](02_target_technology_stack.md#L7-L45)、[当前 Compose](../compose.yaml#L15-L102)。

## 判断边界

界面美观具有主观性；本记录的具体布局与可访问性问题有页面观察和源码定位，尚未进行真实用户任务研究。Agent 截图的 API 响应是模拟数据，不能证明真实科研流程的体验或性能。没有测量模型准确率、检索召回率、延迟和并发吞吐。这里的“未见调用”表示在当前仓库 `backend/app` 生产 Python 源码中未找到调用路径，并非证明外部私有部署不存在扩展。
