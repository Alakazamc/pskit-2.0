# Phase 0：当前代码审计

日期：2026-10-10（Asia/Shanghai）。任务来源：[完整整改任务书](REMEDIATION_SPEC_2026-10-10.md)，原附件 SHA256 `12e6dbb96903b7105e317834a47ddbe88a7efda8b19b6ec73f6fe9f07125169e`，639 行，已完整阅读。

## 基线、范围与方法

- 本地分支 `codex/new-stack-baseline`，HEAD 与 `git ls-remote origin refs/heads/codex/new-stack-baseline` 均为 `5d703951d5ed50302e59c1f91e181ada05af81bd`；没有比任务书更新的已提交修复。
- 开始时有 `.gitignore`、`AGENTS.md`、`docs/DEV_CLOUD_CONTEXT.md`、`docs/ALLINLUNA_WORKFLOW.md` 及 `docs/performance/` 的既有未提交内容，全部保留；不归入业务整改 diff。
- 三个原生子代理分别只读审计前端、后端和部署/CI；主代理核对代码、远端 SHA、编译检查和最小测试。当前宿主不支持完整 All in Luna 顶层 Lane，未伪造编排或回执。
- Phase 0 只读业务源码；仅新增审计/计划文档及原规格的逐字副本。没有 SSH、生产发布、真实数据库变更、真实模型/GPU 调用。
- `confirmed` 表示当前源码可证实；`already_fixed` 表示该具体问题已修好；`not_reproduced` 表示没有证实所声称的缺陷；`needs_live_validation` 表示不能从源码证明有效生产行为。部署组合来自源码与既有发布证据，不等于本次在线 inspect。

## 逐项结果

| ID | 状态 | 当前证据与整改边界 |
| --- | --- | --- |
| D-01 / P0 | confirmed | `deploy_to_cloud.sh:151` 调用全栈 down/up；`deploy/agent/stack.sh:219` 停 Agent/LiteLLM/Supabase，`:165` 的 up 还 provisioning/migrate。安全 app-only 操作已存在于发布 runbook，不能据此认为根脚本安全。 |
| D-02 / P0 | confirmed | `deploy_to_cloud.sh:188` 使用 `/api/v1/health`，`:189` 失败仅 warn，`:269` 仍宣布成功；正式路由 `new_backend/app/api/health.py:10`、`:16` 是 `/health/live`、`/health/ready`。 |
| D-03 / P0 | confirmed，局部保护已存在 | 根脚本 `:83` 回退公开测试 key，`:114` 活动任务预检是人工占位；`stack.sh:119` 已拒绝一个测试 key，但在根脚本 down 后才检查。不能视作完整的发布 fail closed。 |
| CI-01 / P0 | confirmed | `.github/workflows/ci.yml:4` 只覆盖 main；`:14`、`:43`、`:83` 测旧 Python3.11/Vue/根镜像。新 Python3.12/React/Pi/PG17/协议测试没有门禁。 |
| S-01 / P0 | confirmed（模板）；needs_live_validation（生产） | `host-nginx-agent-aliyun.conf:30` 为 dry-run；`cloud.backend.env.example:8` 为 observe。`auth_protection.py:153` 的 enforce 与 `domain/auth_abuse.py:100` 的原子 PG 限流已实现；GoTrue 也有内层限额。不能声称生产全无保护或已强制执行。 |
| F-01 / P0 | confirmed，已复现 | `events.ts:115` 只清本批 textDelta，`:144` 再追加 previous.text，`:165` 留旧错误；三批 old → reset → new 得到 oldnew。另有同批事件 ID 重复导致 xx 的缺陷。 |
| F-02 / P1 | confirmed，基础边界已复现 | `useRunEvents.ts:37` 吞非卸载错误，`:43` 按 50/200ms 重试；`http.ts:174` 丢状态/Retry-After，`:220` chunk 内 CRLF 归一化丢跨块分隔，`:214` 在消费完整帧之后检查上限，超大完整帧可绕过。 |
| M-01 / P1 | confirmed | `model_catalog.py:11` 只有 id/images/reasoning；`model_policy.py:171` 发布不要求 LLM qualification。现有 `admin/qualification.py` 是 MCP Tool Product，不是 LLM。额外问题 `model_catalog.py:158` 用 any 推广混合 alias 的 vision 能力，reasoning 已使用 all。 |
| M-02 / P1 | already_fixed（PinCC 原故障）；confirmed（生命周期工作） | `infra/litellm/provider_compat.py:24` 限定 HTTPS PinCC/native messages，`:52` 固定1.100.3，`:57` 仅 Anthropic；现有测试包含真实 LiteLLM 转换同步/异步、流/非流及官方 Anthropic 对照。缺 CI、OpenAI 对照与退役探针。既有真实验收记录此次未重新在线验证。 |
| M-03 / P1 | confirmed | `pi_rpc.py:166` 内联 models JSON，`:182` 固定 openai-completions，`:172` 手工 reasoning 映射。现有 Run-selected model/vision 绑定应保留，先抽 Config Builder；Responses-only 暂不实现。 |
| M-04 / P1 | confirmed（生命周期）；needs_live_validation（性能影响） | `internal.py:216` 未注入时每请求建 client；`main.py:356` lifespan 未安装/关闭 gateway client；`session_titles.py:87` 独立 client。可复用连接池但标题权限与账本仍独立。 |
| M-05 / P1 | confirmed | `pi_rpc.py:30` 按错误字符串分类；`internal.py:232` relay 上游错误 body；LiteLLM 配置 drop_params=true。现有 body cap、图片数、Run owner、模型授权、去伪 user 已实现，不重复重写。 |
| A-01 / P1 | confirmed（源码成本）；needs_live_validation（PG/生产归因） | `agent.py:140` 50ms 调度，心跳/恢复已按1s节流；`runs.py:134`、`recovery.py:110` 先全量候选；`agent.py:328` 每 delta append，`runs.py:381` 多次 SQL。需本地隔离 PG 基准，不能把源码推断写成线上 P95。 |
| A-02 / P1 | confirmed | AgentService782行、CatalogStore987行；`agent.py:114`、`:531`、`:651` Session锁无回收。Task dict 已回收。先保留 facade 拆职责，再考虑目录。 |
| DB-01 / P1 | confirmed | `postgres.py:129` 替换 SQLite SQL，`:144` 物化结果，`:105` 事务 thread-local。保留 admission advisory lock；不能把 SQL 零散 to_thread 或盲目 SKIP LOCKED。 |
| C-01 / P1 | confirmed | `pi/extension.js:285` 只有泛化 submit_compute，`:297` 模型填 version/budget；已有 per-user capabilities 与 Pending→terminate→trusted resume。业务工具投影必须复用账本和幂等 ID。 |
| X-01 / P1 | confirmed（控制面权限）；needs_live_validation（有效隔离） | OpenSandbox Server 挂 Docker socket，runtime network internal；已有 runsc、禁 host binds、drop caps、无 socket/secret/DB 网络探针。没有证实沙箱逃逸；独立执行节点是后续拓扑决策。 |
| R-01 / P1 | confirmed | 根 README/CI/根 Dockerfile/Compose 仍指旧栈；现行、迁移、回滚配置并存。先澄清入口与引用，再纯路径迁移；不删除历史工具。 |
| F-03a / P2 模型展示 | confirmed | `ModelPicker.tsx:11` 通过 alias.split('/') 猜供应商；public ModelOption 没权威 display 字段。可兼容地增加可选元数据，现有字段保持。 |
| F-03b / P2 上传假进度 | already_fixed | `Composer.tsx:127` 初始 null、`:181` 不定进度；`MonoWorkspace.tsx:263` 完成才100。现有不定进度测试通过，不增加假的中间百分比。 |
| F-03c / P2 历史/CSS/草稿 | confirmed（实现）；needs_live_validation（视觉/容量） | 全量消息读取/map；base/monochrome/mono-pages 重复主题变量；草稿按用户/会话白名单已正确，但无 TTL/手动清理/隐私选项。不能静默删除用户草稿。 |
| A-03 / P2 | confirmed（单机边界）；not_reproduced（现有单机可靠性故障） | `pi_rpc.py:111` 本地 transcript，`runs.py:636` 保存路径；Compose 持久/data已实现。记录单主机共享卷边界，本轮不造跨机 checkpoint store。 |

## 保留的正确机制

模型发布修订/撤销、逐请求用户授权、服务端身份注入；Run/session/global/user admission、lease fencing、原子 checkpoint、禁止重试已有副作用；Compute Pending 后在原 Session 的隐藏可信续跑；workspace cancel 等待终态 ACK、歧义记 unknown；OpenSandbox gVisor 和隔离卷；浏览器草稿账号隔离、附件引用恢复、富 UI Artifact 鉴权。

## 新发现与最小复现

- 根脚本 `rsync -a --delete` 与“先 assets 后原子 index”的注释不符，回滚也删旧 assets；旧页面 hash 引用可能失效。
- runbook 的具体 backend 更新示例漏列现行 OpenSandbox overlays，尽管文字要求沿用实际组合。新命令必须保留完整 overlay 清单。
- 同批重复 ID 未加入 seen：delta x 两次变 xx；跨批 reset 后 previous error 未清。
- 在真实 http.ts 上注入 fake fetch：`["data: JSON\r", "\n\r", "\n"]` 交付0事件；完整1,010,000字节 frame被交付；HTTP429丢 Retry-After42。
- 既有测试漂移：events summary字段、Composer技能空状态文案、Dockerfile新增PIP_INDEX_URL。必须区分旧失败与新回归，禁止简单删断言让 CI 绿。

## Phase 0 验证结果

环境：Python3.12.9、Node24.13.0、npm11.6.2、Vitest5.0.3；使用已有本地依赖，未重装业务依赖。

| 检查 | 命令/范围 | 结果 |
| --- | --- | --- |
| 远端最新 SHA | git ls-remote origin refs/heads/codex/new-stack-baseline | 与 HEAD 相同 |
| Python语法 | ast.parse(new_backend/app及pskit_compute) | passed，164文件，不生成pyc |
| 新前端类型 | cd new_frontend && npm run typecheck | passed |
| 新后端lint | .venv/bin/ruff check app tests --statistics | failed，test_workspace_prompt.py:1 的1项I001 |
| SSE/reducer基线 | npm test -- --run src/features/chat/events.test.ts src/features/chat/useRunEvents.test.tsx src/api/http.test.ts | 35 passed / 2 failed，3.19s，旧summary精确对象断言 |
| Composer基线 | npm test -- --run src/features/chat/Composer.test.tsx | 15 passed / 2 failed，4.56s，旧空状态文案；不定上传测试passed |
| 部署shell语法 | bash -n 根部署、stack/staging与scripts/*.sh | passed，15脚本 |
| 后端最小组合 | pytest test_run_leases.py test_model_catalog.py test_admin_model_policy.py test_session_titles.py；临时cwd、mock、清除继承DSN | interrupted，15个进度点后不结束；不是完成的通过结果，需逐文件加timeout定位 |
| reducer局部增长 | Node直接调用真实投影函数，1000/5000次单事件更新 | 25ms/662ms；仅本地复杂度参考，不是线上P95 |
| PG17/镜像/Compose | 本次未建本地PG或镜像；沙箱内docker命令被snap权限阻止 | not tested，后续仅独立本地实例 |
| 在线防护/真实新供应商/gVisor | 未连接生产或供应商 | needs_live_validation；不把Mock通过当真实资格 |

## Gate 与下一步

Phase 0 源码审计已完成，基线并非全绿。先实施 Phase 1：F-01/F-02 基础修复、退出不安全根部署入口，补 fail-closed 生产配置校验与回归。随后 Phase 2 建新栈 CI，并定位/修复既有失败。具体文件、验证、提交与回滚见 [分阶段实施计划](REMEDIATION_PLAN.md)；部署配置见 [矩阵](DEPLOY_MATRIX.md)，未解决风险见 [风险登记](RISK_REGISTER.md)。生产有效防护、新供应商和沙箱验收需要单独授权，本轮只能生成验证入口和手册。
