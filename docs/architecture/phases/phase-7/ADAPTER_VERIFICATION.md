# 持久准入与接收器适配器：本地验证

日期：2026-10-10。分支 `codex/new-stack-baseline`。本轮是用户“开始干吧”授权的本地续作，遵守原任务的生产、数据和目录边界。宿主缺少 All in Luna 顶层 Task 接口，使用独立原生 subagent 分工与主代理整合，没有创建 Lane 或伪造回执。

最终业务源码：`5ccc25965cd1abd689196a2dfb998f499f83a051`。测试在提交前冻结同一份业务源码；原始 benchmark/smoke 中的 HEAD 表示测量时基线，逐文件摘要才定位当时尚未提交的内容。持久验证摘要及原始日志/JUnit 的 SHA256 见 [verification-summary.json](evidence/verification-summary.json)。

## 环境与隔离

- Python 3.12.9；Node 24.13.0；Pi 0.87.1；专用 PostgreSQL 17.6。
- PostgreSQL 容器仅监听 loopback 32769、使用 tmpfs；测试为每组创建自己的随机 schema。没有使用真实数据库、用户、模型或 GPU。
- `scripts/ci_new_stack.py` 冻结源码快照，排除私有 `.env`、数据、虚拟环境、用户文件；backend/deployment 检查强制执行真实 PG 测试，不允许静默 skip。
- Docker CLI 使用本机 Snap 兼容 wrapper。镜像测试关闭网络；Compose 只渲染合成配置，不执行 `up`。

## 整合门禁

| 门禁 | 结果 | 本机原始证据 |
| --- | --- | --- |
| Backend Ruff/编译/全量 | 1204 passed / 1 skipped / 266.24s；真实 PG 232 passed / 0 skipped | `/tmp/pskit-adapter-final-backend-verified/backend.{log,xml}` |
| Frontend typecheck/lint/Vitest/build | 53 文件 / 387 passed / 50.73s | `/tmp/pskit-adapter-final-frontend/frontend.log` |
| 部署、控制器、CLI、CI 与 benchmark 单测 | 448 passed / 20.89s | `/tmp/pskit-adapter-final-deployment-verified/deployment.log` |
| Pi 扩展 | 9 passed / 0 skipped | `/tmp/pskit-adapter-final-pi/pi.log` |
| OpenAPI/Event/Compute 与 TS 再生 | 无漂移 | `scripts/check_new_stack_contracts.py` |
| 合成 Compose | 9 个组合通过；仅 render | `/tmp/pskit-adapter-final-compose.log` |
| 最终本地镜像 | 3 passed / 4.01s；11 个关键文件哈希一致 | `/tmp/pskit-adapter-image-verified.xml` 与本目录 evidence |
| 显式准入安装 smoke | 1 个合成 turn 完成；不是性能样本 | `/tmp/pskit-adapter-explicit-migration-verified.json` |

唯一 skip 为 `test_profile_round_trip_with_real_private_supabase_storage`，要求另行配置可销毁账号的 Supabase Auth/Storage；没有记作通过。PG 无跳过。FastAPI TestClient deprecation、既有 UI fixture 警告与大 chunk warning 如实保留。Molstar 18 个 lazy chunks、最大 436643 bytes、无初始 import；没有通过提高阈值隐藏其它资源告警。

本轮没有修改前端/Pi 源码；上述检查针对当前整合源码执行。部署 tests、窄回归、专项 PG 与全量 Backend 存在重叠，不能相加宣称更多独立测试。固定 LiteLLM 1.100.3 的断网协议验收属于上一轮，本轮未再次调用容器或真实供应商。

## 确认过的失败及修复

| 缺陷 | RED 证据 | 修复与 GREEN 范围 |
| --- | --- | --- |
| 高隔离级别旧快照可看到旧 OPEN | `/tmp/pskit-admission-repeatable-red.xml` | 状态行 `FOR UPDATE`，旧快照序列化失败；实际 PG 永久回归 |
| 解除闭锁已提交但应用确认丢失 | `/tmp/pskit-admission-release-proof-red.xml` | 恢复同 owner 的闭锁；无法再确认时保留 pending、不返回成功 |
| private proof 接线为两个 `None` 却成功 | `/tmp/pskit-admission-missing-capacity-red.xml` | 必须是实际共享容量；错误接线返回 503 |
| 管理员资格执行绕过共享账本/错误转换 | `/tmp/pskit-admission-qualification-api-red.xml` | 共享 slot 与调用账本；闭锁仍返回既有全局 503 错误码 |
| 普通核心迁移自动装入可选组件，破坏历史导入 | `/tmp/pskit-admission-data-migration-red.xml` | 保持原有核心 13 版本，组件显式安装；原有字节/顺序/下一次插入断言保留 |
| staging 失败清理删除闭锁、成功切换搬移闭锁 | `/tmp/pskit-admission-stage-race-red.xml`、独立短 PG 复现 | activation 同时锁定 staging/target 并重查；cleanup 持锁，发现任何组件表就保留 staging |
| Receiver 父目录换位导致错误零状态 | `/tmp/pskit-receiver-column-default-green-final/backend.xml` 中永久回归 | 固定父目录 FD 与前后身份检查，换位变 unknown；原 waiting 行不变 |
| 首个文本增量重复 lease preflight | [首增量诊断](../phase-4/FIRST_DELTA_DIAGNOSIS.md) 中 RED/ABBA 原始样本 | 仅移除文本独立 preflight，事务内 user/owner/status/expiry fencing 保留 |

首个整合全量为 **2 failed / 1194 passed / 1 skipped**，保存于 `/tmp/pskit-adapter-final-backend/backend.{log,xml}`，不是最终成功证据。后续专项迁移组 39 passed、两项 same-owner 验证通过；全部并入最终全量。没有删除失败断言、忽略准入表或扩大 skip。

另有 receiver/auth 专项 172 passed、qualification wrapper 与旧资格协议组 30 passed、私有证明组 49 passed。它们用于定位和复核，最终整合以门禁表为准。真正无法启动的后端无法提供应用证明，控制器会保留 closed/pending；另行审阅宿主恢复流程，不能把测试替身当作自动解锁资格。

## 本地镜像与证据

标签为 `pskit-agent-backend:remediation-adapters-local-20261010`，另加源码版本别名 `pskit-agent-backend:remediation-5ccc259`，两者 ID 相同。使用 `deploy/agent/backend.Dockerfile` / `new_backend` 正式 context、固定锁文件和已有基础镜像 digest；没有借用生产容器覆盖代码。非 root、Python 3.12、Pi 0.87.1、核心 imports、可写数据目录及无 `.env`/数据库文件检查通过。

最终 image ID 为 `sha256:bf71cd30122d304079a6be5c7f832b2e48ac4d6503fafb51d36156bbcd9493ad`。构建日志为 `/tmp/pskit-adapter-image-build-verified.log`。四个新增源码/测试文件仅清理了 EOF 多余空行后，重新冻结并执行最终 Backend CI、部署检查和镜像构建；早期未规范化的检查证据保留，不能将它的不同 SQL 字节哈希当成最终制品。

只读、断网容器另外逐字节核对 11 个关键文件，包含准入 SQL、installer、核心迁移、私有 proof、资格包装器、历史迁移脚本、首增量 writer 和接收器观察。构建及核对清单保留在本机原始证据与本目录 evidence 中。镜像仅保留本地，不上传或部署；没有生成标称 host preflight/smoke 已通过的 Release Manifest。

验收结束后按标签、tmpfs 与 loopback 绑定核对，再移除唯一属于本轮的 `pskit-remediation-adapters-pg17-20261010`。原有服务/数据未清理；日志、本地镜像与源码证据保留。

## 性能与边界

同源 ABBA 合成对话共 200 次，开启门禁组 admission→首持久 delta 中位数增加约 **3.971ms**；文本 emit→commit 中位数基本相同。P95 有宿主噪声，不能解释为门禁改善尾延迟。早期优化对照、全原始样本、逐文件 SHA 与测量后补丁边界见[诊断报告](../phase-4/FIRST_DELTA_DIAGNOSIS.md)。最后单样本 smoke 不并入这些测量。

## 回退与未验收项

| 实现提交 | 职责 | 本地回退边界 |
| --- | --- | --- |
| `eb2b37b` | 可选 Manifest 准入资格元数据 | 保持旧 Manifest/Web 兼容；不要移除已启用宿主的资格约束 |
| `e70b79a` | 持久准入、CLI、MCP/管理资格边界、迁移状态保护、控制器认证客户端 | 仅默认未启用时可按代码提交回退；保留数据库组件和全部状态 |
| `c82debe` | 现有接收器的认证只读 listener | 先在单独宿主操作中停用该可选观察，保留 journal/outbox/spool |
| `5ccc259` | 首增量最小快路径、benchmark、提交边界回归与原始证据 | 可独立还原 writer 快路径；不需要数据迁移 |

- 默认不启用的新组件可按独立提交回退；首增量快路径可单独还原，保留事件和账本。控制器的准入与 receiver 认证读取共享一个文件，其服务端 listener 是独立提交。
- 已启用的部署只能使用同资格、同绑定、识别该组件的镜像回退。保留 owner/nonce、unknown 调用、journal/outbox、历史事件及工作区，不执行 drop/reset 或降级核心 schema。
- 第一次组件安装、数据库授权、真实 Compose adoption、WireGuard listener、当前/候选/回退镜像的独立资格、Staging API/Web/崩溃/回退与 RPO/RTO 演练尚未执行。
- 真实供应商/Pi/OpenSandbox、生产实际限流、上传性能、大历史 PG EXPLAIN 与纯路径搬迁继续是独立门禁。本轮没有 SSH、生产/Staging 部署、真实配置修改、历史删除或 push。

重跑入口是 `scripts/ci_new_stack.py`，使用新建的专用 loopback PG17，不能换成生产 DSN。详细配置与恢复限制见[发布控制手册](../../../runbooks/RELEASE_CONTROL.md)。
