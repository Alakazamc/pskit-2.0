# PSKit 2.0 整改整合报告

## 当前补充：2026-10-12 整改执行

本轮基线为 `c37ca61e7a8d9672013597f2cc339f4e9060720f`，分支仍为 `codex/new-stack-baseline`；执行[最新整改方案](REMEDIATION_EXECUTION_2026-10-12.md)。`46c1dc4` 阶段之后新版源码已完成独立纯路径迁移至 `apps/api/`、`apps/web/`，本轮**不再次迁移**。根目录旧 `backend/`、`frontend/` 与旧 CI/回退资料继续保留；下文历史报告中的 `new_*` 和“当时未迁移”描述是其对应时间的事实。

PR-0/1 已独立提交 `f088128`、`eb8df70`，基于指定 Actions 失败日志复现并修复 Ruff、前端图片 fixture 和乐观消息刷新生命周期；对应 HEAD 的 [GitHub 六项 CI 全部成功](https://github.com/Alakazamc/pskit-2.0/actions/runs/38156874714)。实际 RED/GREEN、400 项前端、Backend1206/PG232、当前镜像及回滚见 [PR-0/1 报告](phases/2026-10-12-pr01/REPORT.md)。PR-2 安全摘要和独立 MCP 容量/取消修复已通过 [精确 HEAD 六项 CI](https://github.com/Alakazamc/pskit-2.0/actions/runs/38160933404)。PR-3 核心协议资格及普通代理参数一致性已通过 [六项 CI](https://github.com/Alakazamc/pskit-2.0/actions/runs/38162074647)；默认资格证据旧格式读取还有独立兼容复核补丁。PR-4 内部拆分已通过本地458项部署、9组合成Compose和31方法等价；PR-5 默认/历史工具开关已准备并通过定向回归。后续最终源码、远端结果与回滚按阶段索引补齐，未提交或未通过门禁的结果不算交付。

当前部署入口为 [deploy/agent/README](../../deploy/agent/README.md) 与 [当前部署矩阵](DEPLOY_MATRIX.md)。本轮没有 SSH、部署、读取生产私有配置、修改真实数据库或使用付费模型。2026-10-11 的既有生产发布资料记录当时状态，本轮未重新在线核对；本地合成测试不能取代宿主 adoption、真实模型资格、Staging 回滚或 RPO/RTO 演练。

## 历史报告：2026-10-10 第一轮与续作

日期：2026-10-10（Asia/Shanghai）。分支 `codex/new-stack-baseline`；原基线 `5d703951d5ed50302e59c1f91e181ada05af81bd`。下文第一轮结果对应业务源码 `ff873b20cdc5ecd81149f2e63542b76a32537c89`；用户随后授权继续，本地准入、接收器与延迟修复见末尾续作及其独立证据。

## 审计与实施顺序

完整读取639行任务书后，先提交并提供[Phase0审计](CURRENT_STATE_AUDIT.md)、[部署矩阵](DEPLOY_MATRIX.md)、[风险登记](RISK_REGISTER.md)及[逐阶段计划](REMEDIATION_PLAN.md)，再修改业务代码。基线确认了跨批重试文本残留、SSE解码/重试、危险日常发布、新栈CI缺失、多模型/调度/模块耦合；原PinCC限定修复和不定上传进度已存在，保留而未重写。

使用用户要求的原生subagent分工、主代理整合及独立审查；宿主不支持完整All in Luna Lane接口，没有创建虚构Run或完成回执。既有All in Luna配置和性能资料未并入整改提交。

## 分阶段交付

| 阶段 | 本地实现/结果 | 变更、验证与回滚 |
| --- | --- | --- |
| Phase1 / P0 | SSE顺序重置/去重、有界解码与分类重连；禁用危险旧发布入口、强制配置预检、正确ready路径与非零失败 | [变更](phases/phase-1/CHANGE_SUMMARY.md) · [验证](phases/phase-1/VERIFICATION.md) · [回滚](phases/phase-1/MIGRATION_AND_ROLLBACK.md) |
| Phase2 / P0 | 新栈分支/PR CI，PG17不得静默skip，独立契约再生、固定SDK/镜像与合成Compose门禁 | [变更](phases/phase-2/CHANGE_SUMMARY.md) · [验证](phases/phase-2/VERIFICATION.md) · [回滚](phases/phase-2/MIGRATION_AND_ROLLBACK.md) |
| Phase3 / P1 | Runtime Profile/资格复用现有ModelPolicy，共享transport/Pi配置、未知预占、默认关闭的受控host driver | [变更](phases/phase-3/CHANGE_SUMMARY.md) · [验证](phases/phase-3/VERIFICATION.md) · [回滚](phases/phase-3/MIGRATION_AND_ROLLBACK.md) |
| Phase4 / P1 | 原路径内部拆Agent/Catalog、引用计数锁、有界公平候选/异步事务、delta合并、服务端业务工具绑定 | [变更与指标](phases/phase-4/CHANGE_SUMMARY.md) · [验证](phases/phase-4/VERIFICATION.md) · [回滚](phases/phase-4/MIGRATION_AND_ROLLBACK.md) |
| Phase5 / P2 | 前端hooks/Sidebar拆分、权威模型显示、旧API兼容分页、草稿隐私设置、统一主题源 | [变更](phases/phase-5/CHANGE_SUMMARY.md) · [验证与截图](phases/phase-5/VERIFICATION.md) · [回滚](phases/phase-5/MIGRATION_AND_ROLLBACK.md) |
| Phase6 / 独立目录决策 | 按规格例外保留new_*；仅独立提交新README和原文归档，未搬目录/删除旧代码 | [决策/引用清单](phases/phase-6/CHANGE_SUMMARY.md) · [验证](phases/phase-6/VERIFICATION.md) · [回滚](phases/phase-6/MIGRATION_AND_ROLLBACK.md) |
| Phase7 / 受控运维准备 | pskitctl只读入口与Manifest驱动API/Web发布器、准入保护/保守会计、崩溃恢复/旧assets保留、故障注入 | [变更](phases/phase-7/CHANGE_SUMMARY.md) · [验证](phases/phase-7/VERIFICATION.md) · [回滚](phases/phase-7/MIGRATION_AND_ROLLBACK.md) |

第一轮业务提交与契约再生、纯目录文档决策分别提交。第一轮没有新增数据库schema/迁移、框架、微服务或平行模型权限/账本系统。续作显式增加单独 version 1 的发布准入组件，核心 schema 仍为13；仅在专用合成数据库安装，没有迁移真实数据库。

## 第一轮本地验收（源码 ff873b2）

环境：WSL，Python3.12.9、Node24.13.0、npm11.6.2；实际安装Pi0.87.1；专用loopback/tmpfs PostgreSQL17.6、每组随机schema。源码快照排除私有.env、数据、用户文件，测试不读取生产配置。

| 门禁 | 实际结果 | 可复现入口/证据 |
| --- | --- | --- |
| 后端Ruff/编译/全量 | **1079 passed / 1 skipped / 221.60s**；其中 **PG199 passed / 0 skipped** | `scripts/ci_new_stack.py backend --timeout 600`；`/tmp/pskit-remediation-final-backend-green/backend.{log,xml}` |
| React typecheck/lint/Vitest/build | **53文件387 passed / 50.77s** | `scripts/ci_new_stack.py frontend`；`/tmp/pskit-remediation-final-frontend/frontend.log` |
| 部署与CI runner | **349 passed / 18.23s**；含真实PG会计边界19项 | `scripts/ci_new_stack.py deployment`；`/tmp/pskit-remediation-final-deploy-current/deployment.log` |
| Pi扩展 | **9 passed / 0 skipped** | `scripts/ci_new_stack.py pi`；`/tmp/pskit-remediation-final-pi/pi.log` |
| LiteLLM1.100.3断网容器 | **5 tests / 12 wire子场景**通过，真实SDK转换/回调 | [固定网关命令](../../infra/litellm/COMPATIBILITY.md)；PinCC/官方Anthropic/OpenAI同步/异步、流/非流 |
| 当前源码正式context镜像 | **3 passed / 4.83s** | `deploy/agent/tests/test_backend_image.py`；`/tmp/pskit-remediation-final-image.xml` |
| 合成Compose | **9个组合通过**，仅render | `scripts/ci_render_compose.py`；本地/Staging/生产+OpenSandbox+独立Supabase/LiteLLM |
| 契约 | 再生无漂移；旧 **112 paths / 170 schemas**保留，无旧DTO新required字段 | `scripts/check_new_stack_contracts.py`，与基线OpenAPI结构比较 |
| 实际浏览器 | **8组通过**，桌面/手机、明暗、中英文，120条合成消息 | [结果JSON及16张截图](phases/phase-5/screenshots/results.json) |
| 独立审查 | Catalog32方法AST一致；前端7项与资格并发/生命周期/恢复问题修复后重新复现通过 | 各阶段VERIFICATION；Driver接线独立31项通过，目标组合72项通过 |

唯一后端skip为 `test_profile_round_trip_with_real_private_supabase_storage`，需要另外配置独立Supabase Auth/Storage。它没有被算作通过；PG没有跳过。既有FastAPI TestClient deprecation、部分mock UI fixture警告和大chunk warning保留。Molstar18个lazy chunks、最大436643 bytes且无initial import；没有掩盖其它大资源warning。

首轮基线及中间失败保留在阶段证据：不通过删除断言/扩大skip让CI变绿。最后整合的6个Docker错误由本机Snap看不到/tmp且漏CLI wrapper引起，纠正环境后重跑。并发预算、关闭后的代理、缺artifact.created、内部资格崩溃退款均有明确红→绿证据。

验收结束后已精准停止本轮6个mock API/Vite相关进程，并删除本轮带remediation-local标签的tmpfs PG17容器；其它既有服务不变，日志/截图/本地镜像保留。复测请按CI手册新建专用PG17，不能改用生产DSN。

本机命令需 `PATH=/tmp/pskit-ci-docker-bin:$PATH` 适配Snap CLI；GitHub普通Docker无需此包装。Backend/deployment测试只接受专用loopback PG17 DSN。远端GitHub CI未运行，本次没有push。

## 第一轮本地镜像与回退边界

- 本地tag：`pskit-agent-backend:remediation-ff873b2`。
- ID/RepoDigest：`sha256:a86ea08523b415f31a7ce4fbbe81fd2d2f6ab0b9555b9f5a30dd1f0bb8d7045e`。
- 官方 `deploy/agent/backend.Dockerfile` / `new_backend` context；基础Python3.12.12/Node22.21.1的digest未变化，复用本地缓存，安装仍受固定lock约束。非root、Pi版本、核心imports、无秘密.env/数据库文件均通过。
- 该镜像**仅本地验收**，未上传/部署。没有制造标称Staging/preflight/smoke全passed的Release Manifest；外部门禁未通过时不得发布。
- 回退按各阶段单一职责提交revert、旧image/dist和Manifest恢复；不降级数据库或删工作区。启用内部资格后回退driver前须先终止/核对资格Run，保留unknown账本，不能让旧普通调度器重放。

## 性能结果与保留风险

合成PG测量：300秒idle SQL从30927降至5972；满用户前缀100/1000候选查询从402/4002降至4/4；100Run排空5.024→2.980秒；256delta INSERT从256降至4；完成后Session locks由100降至0。1000Run新快照全部完成29.572秒，旧基线没有等量完成，不能给虚假倍率。

**首持久增量P50/P95回归：24.896/28.153→34.460/46.586ms**。如实记录，没有声称全部延迟优化。测量快照/源码哈希、观察器影响及原始结果见[基准](phases/phase-4/BASELINE.md)。不是云端或真实模型/GPU容量；追加内部资格purpose fence之后未重新完整benchmark。

## 尚未通过的外部Gate

1. 新供应商真实工具/Pi/OpenSandbox Staging资格、费用和路由头；当前driver默认关闭，离线证据不授予Agent Ready。Responses-only协议有明确拒绝/界面，未实现未经需求验证的转换。
2. 生产真正生效的限流、可信代理链、管理入口/内部404及实际云端配置，未在线核对。
3. API发布适配器的独立宿主资格、真实host adoption、Staging API/Web/Pending/回滚演练和RPO/RTO；本地实现及合成回归不能替代宿主验收。缺少有效适配器证明时发布器拒绝API更新。
4. apps/*与legacy/*纯路径搬迁：source binds、旧回退和实机smoke未验收，按规格例外不执行。
5. 大历史PG EXPLAIN、真实WebGL/Molstar异步展开视觉锚点、真实Supabase/上传性能、DB长期阻塞的取消延迟；本地mock浏览器检查不替代这些。
6. OpenSandbox高权限Docker控制面仍是受信任单机边界，执行节点隔离及transcript HA只写方案，没有在线迁移。

本轮没有SSH/生产发布、真实数据库/密钥修改、历史数据删除、执行节点升级或目录大迁移。上述外部门禁不是代码任务遗漏的成功证明，须在独立授权环境中完成后才可启用相关上线操作。

## 用户授权后的本地续作

按[续作计划](phases/phase-7/ADAPTER_IMPLEMENTATION_PLAN.md)实现持久发布准入 CLI、实际应用的私有证明接口、统一接收器的认证只读观察和首增量重复查询修复。新工具沿用当前 PostgreSQL、接收器进程和 MCP/PDF 共享容量，没有新增微服务或第二套计算账本。默认不启用准入及 Receiver observer；正式启用要求独立资格和宿主 adoption。

最终业务源码为 `5ccc25965cd1abd689196a2dfb998f499f83a051`。本轮整合：Backend **1204 passed / 1 外部 Supabase skipped**（PG **232 passed / 0 skipped**）、Frontend **387**、部署 **448**、Pi **9**、当前镜像 **3** 通过；9 个合成 Compose 通过、契约再生无漂移。镜像 `pskit-agent-backend:remediation-5ccc259` 仅保留本地。提交划分、制品 ID、原始证据、回退与仍待宿主验收的边界见下方续作记录；本轮专用 tmpfs PG17 已精准移除。

独立审查补齐了高隔离级别旧快照、接收器父目录替换、无幂等键 MCP 未知结果、管理台资格执行绕过、解除暂停后确认丢失及错误接线证明问题。全量测试还发现可选组件自动安装破坏历史 SQLite 迁移，现已改为独立显式安装，并在 schema 切换和失败清理时保留意外出现的闭锁记录。公开 DTO/SSE 契约、目录、现有功能入口保持兼容。变更、最新全量验证和各项回退边界见[续作变更](phases/phase-7/ADAPTER_CHANGE_SUMMARY.md)与[续作验证](phases/phase-7/ADAPTER_VERIFICATION.md)。

首增量重新按 ABBA 测量：文本路径删去提交前重复 lease SELECT，保留事务内 owner/user/status/expiry fencing；在同一源码的200次完整合成对话中，持久门禁增加约3.971ms的 admission 中位延迟。P95 波动没有被解释为改善。原始样本、源码摘要和测量后私有证明补丁的边界见[延迟诊断](phases/phase-4/FIRST_DELTA_DIAGNOSIS.md)。真实供应商、云端网络、RPO/RTO和路径迁移仍待独立验收；未push或部署。
