# PSKit 系统整改实施计划

> **For agentic workers:** 按原生 subagent 分工逐项执行；宿主完整接口可用时才启用 All in Luna `delivery`。任务均以独立变更、检查与交接为单位，用 checkbox 记录实际完成。

**Goal:** 修复确定的正确性/部署缺陷，使新栈成为 CI 对象，并在兼容现有协议、权限、账本和沙箱的前提下减少模型与运行时耦合。

**Architecture:** 保留 FastAPI/Pi/React/LiteLLM/PostgreSQL 模块化单体。先局部修复、建立门禁，再在原目录拆内部模块；目录迁移是最后一个独立决策和提交。发布工具与 bootstrap/migrate 分离。

**Tech Stack:** Python3.12、FastAPI/Pydantic/httpx、Pi0.87.1、Node22+/React/Vite/Vitest、PostgreSQL17、固定LiteLLM1.100.3、现有OpenSandbox/gVisor。

**Spec:** [639行原任务书](REMEDIATION_SPEC_2026-10-10.md)，[Phase 0报告](CURRENT_STATE_AUDIT.md)、[配置矩阵](DEPLOY_MATRIX.md)、[风险登记](RISK_REGISTER.md)。本地/远端基线5d70395；此前未提交配置和压测资料保留。

## 全局约束与交付

- `/api/v1` 现有路径、请求/响应字段、状态码和SSE类型保持兼容；不改Required字段或既有语义。新能力优先内部契约，确需新增接口/可选字段时保存旧契约快照并证明旧客户端兼容，不静默修改既有合同。
- 不SSH发布、不操作真实数据库、不删历史数据或数据卷；仅本地独立临时DB、mock/loopback模型、合成Compose。真实供应商/云端验证明确标not tested并保留Gate。
- 不引入Next.js/LangGraph/Redis/Celery/Kubernetes/新网关或第二套ModelPolicy/Scheduler。
- 不把Pi生成文本当工具执行；保留PinCC限定补丁、逐调用授权、额度预占与结算、lease/fencing、Pending→terminate→自动续跑原Session。
- 每阶段产出 `docs/architecture/phases/phase-N/{CHANGE_SUMMARY,VERIFICATION,MIGRATION_AND_ROLLBACK}.md`。每项检查记录命令/环境/通过失败skip；阶段完成与云端验收分别标记。提交按单一职责拆分，目录迁移不含业务修改。
- 测试失败先归因；不通过删安全断言、宽泛skip、将真实PG测试替换Mock来让CI绿。

## 审查焦点

1. 重试、断线、终态与历史恢复交叉时不能重放正文或丢工具/产物。
2. 旧ModelPolicy发布模型必须继续可用，新未经验证的供应商不能自动标Agent Ready。
3. 高活跃用户占候选窗口时其他用户不能饥饿；Session锁还有waiter时不能被移除。
4. OpenSandbox optional overlay/source bind不因API-only发布丢失；不知道活动/unknown状态时fail closed。
5. Model/DB/module拆分与文件移动不能混一个提交，旧回退配置/资源保持可读可执行。

## Phase 0 — 审计交付

- [x] 完整读取规格、核对HEAD/远端/未提交归属。
- [x] 三领域只读审计并记录逐ID状态和既有正确保护。
- [x] 前端最小复现、语法/typecheck/lint/现有测试基线；记录中断与未验证范围。
- [x] 输出审计、部署矩阵、风险登记和本计划；先向用户提供链接与结果，再开始业务代码。

## Phase 1 — P0正确性、SSE基础、部署安全

### 1A. 流式投影与重连（F-01/F-02）

**Files:** 修改 `new_frontend/src/features/chat/events.ts`、`useRunEvents.ts`、`new_frontend/src/api/http.ts`；新建 `new_frontend/src/api/sse.ts`、`new_frontend/src/features/chat/reconnectPolicy.ts` 与对应测试；保留ResearchApi函数签名。

**Interfaces:** `SseFrameDecoder.push(chunk: Uint8Array): SseFrame[]` / `finish(): SseFrame[]`，维护UTF-8与跨块CR状态，单帧限1,000,000 bytes；HTTP适配仍向业务交付既有RunEvent。重连策略消费既有 `ApiError.status/retryAfterSeconds`，返回stop或有界delay；保留cursor。

- [x] 增加跨三批old/reset/new、清旧error、同/跨批去重、多次reset及plan/tool/artifact保留回归，确认先失败。
- [x] 按事件顺序清累积正文和错误；本批也更新seen，现有事件字段不变。
- [x] 增加CR/LF每个拆分边界、UTF-8、comment、多行data、bad JSON、完整/半帧超限回归；在消费前控制最大帧。
- [x] 保留ApiError及Retry-After（秒和HTTP日期）；401/403/404停止盲重试，429按服务器等待，503/网络有上限backoff+jitter；Abort/terminal不告警。
- [x] fake timer验证重连、cursor续接和卸载；更新已漂移summary断言而非删除内容检查。
- [x] 运行目标Vitest、typecheck/lint；独立提交 `fix(frontend-events): ...`。

### 1B. 封闭不安全旧发布路径（D-01..03/S-01）

**Files:** `deploy_to_cloud.sh`、`deploy/agent/tests/test_safe_release_entrypoint.py`；`deploy/agent/stack.sh`和生产env示例的纯预检；`docs/runbooks/DEPLOYMENT.md`与发布runbook。

- [x] 将根旧脚本变为无副作用的非零弃用入口，指向现行app-only操作；不能执行SSH/full down/provision/migrate/rsync-delete/test-key fallback。
- [x] stub PATH测试旧入口所有历史参数（含rollback/帮助），证明未执行外部命令；弃用是行为安全修复，不伪称新完整发布器已实现。
- [x] 生产captcha配置校验覆盖全部官方公开测试key，缺配置拒绝；核对可信proxy和明确执行限流层。保留Nginx dry-run供观测，不能把模板当生产限流验收。
- [x] 文档中的API-only命令显式保留OpenSandbox overlays并使用 `/health/ready`；smoke失败必须非零，DB迁移单独执行。
- [x] 现有auth enforce/429、CSRF/匿名/owner边界回归；生产有效限流列needs_live_validation，不在线改Nginx。
- [x] bash语法/部署单测完成；独立提交 `fix(deploy-release): ...`。

**Gate:** 1A/1B目标回归通过，静态和stub证明日常旧入口不能停库；未满足真实线上防护的部分只列待授权。**Rollback:** revert对应小提交；安全入口不建议恢复unsafe执行，保留已知好制品的手册回退，无DB迁移。

## Phase 2 — 新栈CI与发布制品门禁（CI-01/M-02）

**Files:** `.github/workflows/new-stack.yml`（保留旧栈workflow用于历史维护）、`deploy/agent/tests/test_backend_image.py`、`new_backend/tests/test_workspace_prompt.py`、旧文案测试；`scripts/check_new_stack_contracts.py`、合成Compose fixture/渲染脚本、`contracts/release-manifest.schema.json`及测试。

- [x] 针对目标分支push和所有PR新增Python3.12/PG17、React、Pi、固定LiteLLM协议及新镜像jobs，不连接云端或自动发布。
- [x] Backend运行ruff/compile/单元/PG17；提供独立service DSN，确认PG suite实际执行而非静默skip。定位Phase0中断的组合，逐文件timeout+trace，不猜原因。
- [x] React npm ci/typecheck/lint/vitest/build（含Molstar verifier）；Pi npm ci + extension.test.js。
- [x] 运行OpenAPI/Event/Compute导出与TS再生，git diff契约须为零；如基线生成物已漂移，独立列差异审核，不能与业务改动混淆。
- [x] 固定LiteLLM1.100.3容器执行真实callback的离线PinCC/官方Anthropic/OpenAI对照；不给provider key、不跑付费调用。
- [x] synthetic env渲染本地/Staging/生产/optionalOpenSandbox，检查overlay与隔离；修Dockerfile期待漂移，构建正式context new_backend、非root/Python/Pi/import检查。
- [x] Release Manifest schema校验固定source SHA/image digest/dist hash/schema/Pi/LiteLLM/compat/Compose/checks；manifest不能虚报未执行的ci/preflight/smoke。
- [x] 新CI本地等价命令通过后提交 `test(new-stack-ci): ...`；GitHub远端CI只有push后才有执行证据，不把本地通过写成远端green。

**当前：** 代码与独立审查通过，后端862/PG170零跳过、前端357、部署248、新镜像3和固定LiteLLM容器5 tests/12 wire子场景通过。远端CI和云端验收not tested。详见[阶段记录](phases/phase-2/VERIFICATION.md)。

**Gate:** 独立PG/协议/构建/契约检查有实际结果；失败/不可用明确保留。**Rollback:** revert新workflow/校验工具，无线上和DB变更，旧workflow保留。

## Phase 3 — 多模型兼容与资格（M-01..05）

**Files:** `app/contracts/admin.py`、`domain/admin/model_policy.py`（现有权威）；新 `app/services/model_gateway.py`、`app/domain/admin/model_qualification.py`、`app/adapters/live/pi_model_config.py`；修改model_catalog、main lifespan、internal proxy、session_titles、pi_rpc及对应tests。保持PinCC compat隔离。

- [x] 先修mixed alias vision最低能力；增加未知部署/missingmetadata回归，人工声明不能掩盖已知不兼容部署。
- [x] 提取PiModelConfigBuilder，默认Chat Completions与旧行为一致；Profile revision/协议/能力/qualification证据复用已有ModelDraft修订审计。
- [x] lifespan共享httpx连接池，保留测试注入；proxy/标题共享transport但用途授权/Token预占独立；shutdown/流取消关闭response。
- [x] 结构化上游错误status/code归一化，脱敏稳定错误码；保留原公开错误合同映射。未识别保持unknown，不虚构usage=0。
- [x] qualification离线矩阵：text/stream/tool_calls碎片/tool result继续/429/usage缺失/图片/reasoning各声明；记录alias/deployment/网关版本/URL指纹/证据，无密钥或私密输入。
- [x] 新profile未passed不Agent Ready；旧已发布路由显式兼容，不悄然隐藏用户现有模型。公开新增信息为可选、契约生成与旧客户端兼容单独验证。
- [ ] 接入受控真实host driver：逐部署/每次调用独立账本/Pi写读和产物所有权验证；默认关闭，离线证据不得签发Agent Ready。
- [ ] PinCC限定patch不扩展；通用OpenAI-route不修改工具body。生成真实新供应商Staging验收手册，未授权时not tested。

**Gate:** 本地协议/账本/旧模型回归通过；新provider真实Pi文件/Artifact资格需另授权，在此之前不宣称生产支持已验收。**Rollback:** revert各模型小提交；Profile新revision停用旧revision仍可用，无自动真实DB迁移或credential变更。

**当前：** 核心边界冻结版本 `bba1ee6` 全量969通过/PG170零跳过；实际资格driver仍在实现，完整阶段门禁pending。详见[阶段记录](phases/phase-3/VERIFICATION.md)。

## Phase 4 — Backend运行时与模块边界（A-01/02/DB-01/C-01/A-03/X-01）

**Files:** 原路径下新建 `services/session_locks.py`、`services/agent_scheduler.py`、`services/agent_events.py`、`domain/catalog_skills.py`、`domain/catalog_files.py`、`domain/catalog_tools.py`；现有AgentService/CatalogStore保留facade；runs/recovery的原生PG候选查询独立patch；Pi业务Tool投影独立patch。

- [ ] 先建立隔离PG17的idle/query/write/100与1000排队/首增量P50/P95基准，记录命令、环境和分位；测量未通过不声称性能提升。
- [ ] refcount SessionLockRegistry保留等待者；不同session并发、同session排他、取消waiter、任务结束回收回归。
- [ ] 公平有界候选窗口+cursor，保留admission advisory lock、global/user/session quota和lease；不用直接LIMIT导致前面满用户阻塞后面用户。
- [ ] event wake signal+fallback空闲退避；有界delta合并在工具/Artifact/retry/terminal前flush；critical事件及时持久；崩溃丢失范围需明确，最终账本无退化。
- [ ] 逐事务而非逐SQL迁移异步边界；原生PG只先替高频候选/分页，不扩兼容字符串规则。
- [ ] scheduler/projector/context/executor/resume和Skill/File/Tool内部职责拆分，facade现有签名/导入保持；逐组件回归和提交，不同时搬目录。
- [ ] per-release业务Tool只向模型暴露业务参数；预算/version服务端绑定，保持submit_compute兼容入口、幂等call_id、Pending与trusted resume协议。
- [ ] 记录单机transcript卷与OpenSandbox控制面权限；取消ACK/unknown/隔离mock回归；不做执行节点迁移或HA。

**Gate:** PG并发/幂等/恢复/账本实际通过，给出前后基准；新拆分性能不能只靠行数证明。**Rollback:** 小模块revert，保持DB schema/数据不变；任何确需schema索引变动单列迁移手册，不执行真实DB。

## Phase 5 — Frontend组件与P2（F-03）

**Files:** ModelPicker/MonoWorkspace内部hooks、composerDrafts/设置隐私组件、Conversation分页适配、styles语义token源和回归；保持现有路由/富UI/草稿存储兼容。

- [ ] 使用服务端权威展示信息，不解析alias为provider；已有模型/推理本地偏好不丢失。
- [ ] 上传保留已通过的不定进度，不新增假百分比；按用户提供草稿清理/保留说明，旧草稿兼容且不静默删除。
- [ ] 先增加兼容可选pagination参数且默认旧合同不变，再分批加载历史，验证滚动anchor、展开状态、Artifact/Molstar；不强行虚拟列表。
- [ ] MonoWorkspace拆query/send/run lifecycle/Sidebar，小步保留壳；SSE事件辅助信息先projection后有界保留。
- [ ] semantic tokens单一明暗源，小范围组件迁移；桌面/手机、中英文、键盘与dark/light截图回归，记录实际验证范围。

**Gate:** Vitest/typecheck/lint/build、必要浏览器回归实际通过。**Rollback:** 独立前端提交/旧dist可回退；浏览器存储版本兼容，不删旧数据。

## Phase 6 — 独立纯目录迁移（R-01）

- [ ] 检查Phase1–5门禁、全部引用/Compose等价和现行回滚路径；形成迁移前source-bind/import清单。
- [ ] 风险仍高则保留new_backend/new_frontend（规格明确允许），只补归档索引与未来映射，标迁移Gate pending，不能把准备当已搬迁。
- [ ] 条件满足才在独立纯迁移提交将新栈搬apps/api、apps/web；同时只改路径/import/workdir/COPY/tests/contracts/export引用，不改业务函数。
- [ ] 旧栈需先证明引用方迁好，再归档legacy；不物理删除历史/运维恢复工具。
- [ ] 全套本地CI/Compose/build/import/smoke验证，diff以rename/path变动为主。

**Rollback:** revert纯路径提交或旧完整checkout构建制品；现行云端source-binds不在本轮改动，生产路径切换需另授权。

## Phase 7 — 运维入口与受控发布准备（D/R/X）

**Files:** `deploy/agent/pskitctl`薄入口及`deploy/agent/scripts/release_control.py`、Manifest校验、状态preflight、web原子发布/回滚函数、`docs/runbooks/DEPLOYMENT.md`；复用现有stack/staging配置解析，不新建daemon。

- [ ] doctor/status/render/logs只读；release/rollback明确target env/component/version和不可变Manifest；bootstrap/migrate/executor是独立边界。
- [ ] API只允许同项目完整overlays `up -d --no-deps --wait backend`；检查固定digest/Run/Job/Attempt/unknown、schema只读、ready/smoke；失败返回非零并应用回退，不能降级DB。
- [ ] Web先新assets后原子index/版本指针，保留旧hash资源；模拟中断、缺key、502、stale manifest、OpenSandbox overlay丢失、rollback回归。
- [ ] 总runbook覆盖新维护者从README找到唯一现行开发/发布/回滚入口；旧安装/迁移/历史文档标用途并保留。
- [ ] 生成Staging演练与RPO/RTO/执行节点边界计划；本次不执行云端演练或生产切换。

**Gate:** 本地fake docker/HTTP/临时静态目录故障注入通过，真实Staging与production均pending授权。**Rollback:** Manifest指向旧应用镜像/dist，仅服务/文件回退，state/卷不变。

## 执行顺序与并行归属

Phase1前端与部署可并行、文件不交叉；完成后Phase2由CI负责人整合所有gate。Phase3内transport与Profile通过明确接口依赖推进；Phase4运行时先基准后优化；Phase5依赖Profile展示合同，组件拆分独立；Phase6严格最后纯迁移；Phase7命令可先做设计/fixture，执行上线永远等待独立授权。新增发现先归ID/优先级，不扩大成新框架或微服务。
