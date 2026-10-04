# Skill 注册、版本、触发记录与优化：官方资料研究

日期：2026-10-05。范围：LiteLLM、MLflow GenAI、Pi coding-agent；仅研究设计，未调用模型、部署或修改实现。“Lite ML”所指产品待确认，以下分别给出判断。

资料通过在线官方文档与官方源码核对。LiteLLM/MLflow 引用查询时的当前文档；Pi 固定到本项目依赖的 **0.87.1**，避免把最新版能力当成本项目已有能力。

## 结论

建议 **PSKit 保留 Skill 注册与授权的事实来源，外接可选 tracing/evaluation adapter**。Skill 包的不可变版本、发布、用户授权、Run 版本快照和业务触发记录由 PSKit 管理；LiteLLM 可提供分发目录及模型调用日志，MLflow 更适合执行过程追踪、评估与指令优化。此为架构判断，不是产品原生能力声明。

不能笼统说“LiteLLM 不支持 Skills”：它已有 Claude Code 插件市场，以及向指定 agent runtime 分发本地 Skill 目录的 harness adapter。也不能因 MLflow 有 Prompt Registry 就认定它已提供 PSKit/Pi 所需的完整 Skill 包注册服务。

## 1. LiteLLM 的实际支持范围

| 能力 | 官方支持 | 对 PSKit 的边界与判断 |
|---|---|---|
| Managed Skills | Claude Code Plugin Marketplace 登记 Git/HTTPS ZIP 来源；管理员增删改、启停；可选 version。写操作要求 proxy admin；启用项公开，禁用项仍可按 key/team 授权可见。 | 是真实目录治理能力；对象是 Claude Code plugin。不能直接当作 Pi 的加载/授权接口。[官方说明](https://docs.litellm.ai/docs/tutorials/claude_code_plugin_marketplace) |
| 不可变版本 | 上述 API 的 PUT 会完整替换同名记录；version 可被修改或省略清空；ZIP 可带 sha256。 | 推断：目录版本标签及可选校验值不足以替代 PSKit 的不可变包快照、审批与 Run 固定版本。可将 PSKit 已发布版本导出为目录来源。[同一 API 说明](https://docs.litellm.ai/docs/tutorials/claude_code_plugin_marketplace#api-reference) |
| 本地 Skill 分发 | `litellm.agent(..., skills=[目录])` 将含 SKILL.md 的目录在 session 开始时复制到 runtime。列出的支持对象为 Claude Code、Codex、OpenCode、Deep Agents；Tool Loop 不支持。 | 当前支持表未列 Pi；未验证 Pi adapter，不能宣称该 SDK 能直接接管 PSKit 的 Pi Skills。[Harness tools/skills](https://docs.litellm.ai/docs/harness/tools) |
| 调用关联 | 请求 metadata/tags、调用日志与 `x-litellm-call-id` 可用于关联用户、模型调用、tokens/cost 等。可关闭消息正文日志保留支出信息。 | 网关看到的是模型请求及调用方提供的关联信息，不能仅凭 tags 证明本地 SKILL.md 已加载或业务操作成功。[Proxy logging](https://docs.litellm.ai/docs/proxy/logging) |
| Callback | CustomLogger 有请求成功/失败等 hooks；当前文档另列逐 deployment attempt 的 hooks，可区分 retry/fallback 与逻辑请求。 | 适合模型层事件；Skill 加载、工具调用和业务 Job 需要 PSKit/Pi 另行埋点。选请求级或尝试级 hook 时应避免重复计数。[Custom callbacks](https://docs.litellm.ai/docs/observability/custom_callback) |

集成注意：当前 `generic_api` 文档提供 HTTP 日志出口；Enterprise 文档将标签预算及部分日志治理列为付费能力。普通自定义 Python callback 不应据此被当作 Enterprise 必选；实际接入前按部署版本和具体功能授权核对。[Generic API](https://docs.litellm.ai/docs/observability/generic_api)、[Enterprise 功能](https://docs.litellm.ai/docs/enterprise)

## 2. MLflow GenAI 的适配点

**Tracing：** 官方提供 `@mlflow.trace`、`mlflow.start_span` 和 trace tags，可显式记录父子执行步骤、inputs/outputs、耗时和属性。适合 PSKit 的 Run → Skill 加载 → Tool/Job → LLM 调用链；完整链路需要应用埋点，本次未核实 Pi 自动追踪插件。[Manual tracing](https://mlflow.org/docs/latest/genai/tracing/app-instrumentation/manual-tracing/)

**Evaluation：** 可将生产 traces 交给 `mlflow.genai.evaluate`，评分器检查最终结果及执行轨迹。MLflow 官方还专门展示 Skill 的工具选择、顺序与业务规则评估，并通过 `predict_fn` 包装 agent。[Trace evaluation](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/traces/)、[官方 Skill 评估示例](https://mlflow.org/blog/evaluating-improving-agent-skills/)

**Registry：** Prompt Registry 支持文本/聊天模板及不可变 prompt version；加载 prompt 可关联到 trace。推断：可映射 Skill 的指令正文，但提示模板版本不等于包含脚本、references/assets、工具权限和 PSKit 发布策略的完整 Skill 包。[创建版本](https://mlflow.org/docs/latest/genai/prompt-registry/create-and-edit-prompts/)、[使用及 trace 关联](https://mlflow.org/docs/latest/genai/prompt-registry/use-prompts-in-apps/)

**Optimization：** `mlflow.genai.optimize_prompts` 接收 prompt URIs、predict_fn、训练数据、scorers 和 optimizer；当前文档提供 GEPA/MetaPromptOptimizer。适合在 PSKit 包装下优化指令候选，不是自动改写任意 Skill 脚本、权限或业务契约的承诺。优化结果应经过评估和审批，再生成 PSKit 新版本。[Optimize prompts](https://mlflow.org/docs/latest/genai/prompt-registry/optimize-prompts/)

**LiteLLM 连接：** 官方支持 Python `mlflow.litellm.autolog()`；LiteLLM proxy 也有 MLflow success/failure callbacks。Proxy 日志集成不会自行补齐 PSKit/Pi 的父级业务 spans。普通 proxy 日志与 MLflow 集成示例使用的 metadata 外层字段不同，接入时应验证具体版本的传递与 tag 映射。[LiteLLM→MLflow](https://docs.litellm.ai/docs/observability/mlflow)、[MLflow LiteLLM integration](https://mlflow.org/docs/latest/genai/tracing/integrations/listing/litellm/)

此外，`mlflow skills list/view` 描述的是查看安装中附带的 MLflow Skills，不能据此推断它是任意业务 Skill 的中央注册 API。[官方 CLI](https://mlflow.org/docs/latest/api_reference/cli.html)

## 3. Pi 0.87.1 的加载与禁用机制

- **目录与文件：** 便携结构为目录中的 SKILL.md，附带 scripts/references/assets。标准路径包括 `~/.pi/agent/skills`、项目 `.pi/skills`、`~/.agents/skills`、项目及其祖先的 `.agents/skills`；可通过配置/包附加资源。项目祖先搜索在存在仓库根时止于根。[Skills 文档](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/docs/skills.md)、[skills.ts](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/src/core/skills.ts)
- **Progressive disclosure：** 启动时展示 name/description/path，任务匹配时模型再读取全文；模型可能漏读。显式 `/skill:name 参数` 会读取并展开全文，参数附在用户请求中。[Skills 文档](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/docs/skills.md)、[命令展开源码](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/src/core/agent-session.ts)
- **禁用不是权限控制：** `disable-model-invocation: true` 隐藏自动选择，显式命令仍可用；`enableSkillCommands` 仅影响交互命令发现，手动命令仍有效。`--no-skills` 关闭默认发现，但显式附加 Skill 路径仍会加载。[Skills 文档](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/docs/skills.md)、[resource-loader.ts](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/src/core/resource-loader.ts)
- **Session 边界：** `/reload` 重新加载资源和运行时配置；源码没有在此清除已有对话消息。推断：禁用后不能假定之前加载的指令从上下文消失，实际工具/服务仍须在服务器侧检查授权。[reload 实现](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/src/core/agent-session.ts)
- **工具与审计：** `allowed-tools` 在文档中为实验字段，本版本解析源码未将它建立为工具 ACL；不能依赖它做隔离。正常按需读取需要实际可用的 read/bash 等工具。Extension 有 input、before_agent_start、tool_call/tool_result 等事件；input 在 Skill 命令展开之前。成功展开没有专用的 Skill 成功事件，需自行建立可验证记录。[解析/提示生成](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/src/core/skills.ts)、[Extensions](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/docs/extensions.md)、[展开实现](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/src/core/agent-session.ts)

本项目当前固定 `@earendil-works/pi-coding-agent: 0.87.1`；Pi 启动同时带 `--no-skills`、`--no-builtin-tools`、`--no-context-files` 等参数。已有 Catalog 具备不可变递增 Skill 版本注册。因此，原生目录发现与按需读文件不能直接假定在 PSKit 当前启动方式下已启用。[依赖](/home/jhli/pskit-2.0/new_backend/pi/package.json:1)、[启动参数](/home/jhli/pskit-2.0/new_backend/app/adapters/live/pi_rpc.py:185)、[现有注册入口](/home/jhli/pskit-2.0/new_backend/app/domain/catalog.py:240)

## 4. 推荐设计（待用户确认，尚未实施）

### PSKit 注册与执行事实

扩展现有 registry，版本对象至少保存 Skill ID、不可变版本、内容/包 digest、来源与依赖、工具声明、发布状态、授权和变更审计。Run 选择发布版本后固定快照，优化不修改已发布版本或正在运行的会话。若接入 Pi 目录加载，仅暴露被授权的版本目录，继续关闭环境自动发现；加载内容与执行工具各自受服务器授权控制。

触发记录分别表达以下证据，事件名仅为设计建议：

| 阶段 | 可证明的事实 |
|---|---|
| available | 本次上下文提供了 Skill 摘要；不能计为触发成功。 |
| requested | 用户或路由选择了指定版本，保留选择方式。 |
| loaded | 指定 digest 的全文成功展开/读取；不能证明模型遵循了全部要求。 |
| tool_invoked | 关联实际 tool call 与 Job ID、attempt ID。 |
| completed/failed | 关联实际执行结果、产物和 trace/call ID；与单次 LLM 请求状态分开。 |

这些记录先落 PSKit 的业务账本。可选 adapter 从可信服务端附加 `run_id/session_id/skill_id/skill_version/skill_digest/attempt_id`，同步到 LiteLLM tags 或 MLflow trace 属性；多 Skill 及模型 retry/fallback 不应都算成一次新 Skill 触发。模型 tokens/cost 与计算服务 UsageReport 保持各自的计量来源。

### 可选观测与优化

若用户指 **LiteLLM**，优先接请求关联及模型成本日志；确有 Claude Code 分发需求再导出 Marketplace。若指 **MLflow**，优先接业务 tracing 和离线评估；两者可以并存，不要求替换 Pi runtime。

评估集覆盖应触发/不应触发、正确工具与顺序、授权拒绝、结果与产物、失败恢复、延迟和 tokens/cost。先用确定性评分与现有轨迹，再按明确授权使用需要模型调用的 judge/optimizer。流程为候选 → 独立评估 → 人工审批 → 新不可变版本 → 发布。

Adapter 应可关闭，异步、有界重试、幂等关联；观测服务不可用不阻塞 Run。默认只发送必要关联字段，正文/科学输入/文件内容/凭据须单独配置脱敏与保留策略。这些是 PSKit 设计建议，不是已验证的 SDK 默认保证。

## 研究边界

已核对官方接口、文档和 Pi 固定版本源码；未运行 SDK 或连接实际 LiteLLM/MLflow 服务。在线新功能不代表当前部署的版本或授权已包含。未验证原生 Pi→LiteLLM harness adapter、Pi→MLflow autolog、统一的文件级 Skill registry，故没有承诺这些集成开箱即用。实施前仍需确认“Lite ML”具体产品、部署版本及想集中管理的对象（指令文本还是完整 Skill 包）。
