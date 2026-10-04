# 用户沙箱实现与验收记录

日期：2026-10-04。对应 [用户沙箱实施计划](../superpowers/plans/2026-10-04-user-sandbox.md) A-1～A-5。本记录为本地实现与隔离验收证据；生产尚未启用。

## 已实现

- 每用户一个容器与稳定持久卷；每会话独立 cwd、`.pi` transcript、attempt 和产物目录，同会话串行执行。
- PostgreSQL 保存 owner、revision、活动 lease 和替换操作。lease 到期、manager 重启、bridge 失联或活动信息损坏均保守处理；明确退出才释放活动。
- 取消先进入 `cancelling`，Pi subprocess 完成等待后才记录 `cancelled/exited`。排空阻止新执行，确认活动结束后换容器并保留卷。
- 上传验证会话/file ID 所有权、SHA 与大小，使用目录 handle 和 `O_NOFOLLOW` 拒绝路径穿越与 symlink。产物进入现有 blob 存储，现有 Artifact API 可按 owner 下载；上传与产物共享实际存储额度准入锁。
- backend 通过认证 manager proxy 到达 bridge，保持原有网络；用户容器只接入独立 internal 网络。gateway 仅代理指定 internal 模型/工具路径，Pi 子进程只接收 Run 凭证。

## 替身与真实 PostgreSQL 验证

| 验证范围 | 结果 | 原始记录 |
| --- | --- | --- |
| 沙箱 HTTP/runner、lease、workspace、Compose、环境 | 45 passed | `.superpowers/sdd/2026-10-04-user-sandbox/sandbox-scope-final.log` |
| 邻接 Agent、Pi RPC/并发、catalog/artifact 消费者 | 前一独立范围32 passed；最终全后端603 passed，无skip | 前一执行ledger；后续组合进程卡住记录另保留 |
| 本地 PostgreSQL 临时 schema：并发准入、重启 fencing、产物持久化与幂等 | 4 passed，无 skip | `sandbox-postgres-final.log` |
| Ruff：沙箱运行时、文件 API、消费测试及烟测 helper | All checks passed | 执行 ledger 与工具输出 |

Docker/Pi 替身只位于显式测试边界；live manager factory 必须使用 PostgreSQL。凭据从权限0600的本地测试环境文件读取，没有写入日志。

## 真实 Docker 验收

最终项目 `pskit-sandbox-test-oct04j` 使用不可变应用 image ID `sha256:8f5e3eff1ee44efc92317ee13fe3ab344ad051877b43175306c7f0e13be0594c`，nginx digest `sha256:5a88c9c45479443d7be2eadc894b4ed0a9801bae03d97a5760ae13b5c2005942`。真实 Pi 通过私有 gateway 调用确定性本地 HTTP 模型替身；没有付费推理。

命令：`new_backend/.venv/bin/python deploy/agent/scripts/sandbox_smoke.py --project pskit-sandbox-test-oct04j --image sha256:8f5e3eff1ee44efc92317ee13fe3ab344ad051877b43175306c7f0e13be0594c --gateway-image nginx@sha256:5a88c9c45479443d7be2eadc894b4ed0a9801bae03d97a5760ae13b5c2005942`。

最终 exit0；两用户三会话、真实 Pi、文件字节往返、活动超过缩短的空闲窗口、数据库/backend DNS 与公网出口拒绝、gateway 管理路径403、排空保卷、停止后 transcript 恢复均通过。记录为 `.superpowers/sdd/2026-10-04-user-sandbox/docker-smoke-oct04j.log`，构建记录为 `docker-build-oct04j.log`；前一独立项目 oct04g/h 同样通过。测试数据库从缓存 PostgreSQL16镜像解析到不可变 image ID `sha256:81bd698b4594e751a3269e4dcd3e03a4a0ec0daf7b72e7aa1abd43cce9887542`，实际启动值记录于 smoke JSON。

测试退出时只删除测试容器和网络，用户工作卷保留。产物文件由可信烟测操作者生成以验证真实传输；Pi 内置任意 shell/文件工具仍关闭。

## 调试过程与边界

失败日志 oct04b～oct04f 已保留：Compose tmpfs 引号、nginx 只读 temp 目录、WSL 到 Docker daemon 的 HTTP 路径、旧安装包与 Pi assets 路径、镜像继承 Labels。最初 oct04 日志被 oct04b 覆盖，该失败在 ledger 中注明，不作为成功证据。后续每次使用新项目和独立日志。

执行前的明确 HTTP 验证拒绝会释放未启动的活动 lease，可在同会话重试；422 consumer 先复现残留 lease 再通过。503、冲突和断连仍要求可信退出证据，503 consumer 验证保留未确认活动。

oct04i 的后续复核在真实文件导出时发生50秒 HTTP 超时，失败日志保留。公开并发 consumer 随后复现了同一类生命周期竞争：文件正在传输时 sweep 会停止容器，replace 会提前完成。修复使用同一 PostgreSQL lease 表区分 Pi 与 transfer，保护整个响应并续约；bridge 记录实际传输终态。Pi lease 在 AgentService 收集 callback 期间持续持有，排空状态只允许匹配原已准入 attempt 的 continuation。新执行仍拒绝。两个竞争 tracer 与排空后收集 tracer 均 RED→GREEN；最终 oct04j 实测再次通过。

一次合并回归和一次邻接回归进程在旧 Pi RPC deadline 附近卡住，日志与 faulthandler 输出保留；只中断本任务的测试进程，未将其标为通过。该 deadline test 独立执行1 passed/2.22s，root前一完整后端587 passed，最终全应用以冻结后串行重跑为准。

当前证明的是受限工具模式下的本地 Docker 生命周期和网络隔离。环境清理不等于不同 UID 或内核隔离，任意 shell/Python 尚未开放。真实付费模型、目标生产/Staging 主机与正式发布仍需各自部署流程；本次没有执行发布。启用和 transcript 回退步骤见 [SANDBOX.md](../../deploy/agent/SANDBOX.md)。最终全应用集成测试、代码审阅和 batch commit 已汇总于 [交付记录](2026-10-04-sandbox-admin-delivery.md)。
