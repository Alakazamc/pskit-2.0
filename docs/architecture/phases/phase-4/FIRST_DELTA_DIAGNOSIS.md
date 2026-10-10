# 首个持久增量延迟：本地复测与最小修复

日期：2026-10-10。状态：重复读取优化及同源 admission gate 开关复测完成。没有连接真实供应商、SSH、生产数据库或部署。

## 测量边界与来源

- 同一个 dedicated loopback PostgreSQL 17.6，端口 32769，数据库 `pskit_remediation`，tmpfs。每阶段仅创建、迁移、删除自己的随机 `pskit_benchmark_<uuid>` schema。
- 真实 AgentService、完整 admission、scheduler、仓库、psycopg 和事务；Pi 为延迟 5ms 的合成回调，每 turn 8 个文本 delta，不执行 HTTP、Pi RPC、工具、transcript 或 GPU。会话创建也计入 admission 起点。
- 原版源：`bf3abbc7abcbfdb14f198965fa5d5dd7e6cc2d92`。修复前源：`ff873b20cdc5ecd81149f2e63542b76a32537c89`。优化源为冻结工作区快照，其记录的 HEAD 只说明快照来源；以 JSON 中逐文件 SHA 为准。两轮优化与 ff 对照的运行时 SHA 差别仅为 `app/services/agent_events.py`。
- 首个持久 delta 只在真实 append 方法返回且 batch 结果为 True 后计时。False/drop 不产生样本；保留旧 append_event 返回 None 的兼容路径。
- 每组两轮使用 ABBA 顺序，每轮 50 个完整 turn；全部原始样本保留。没有选择最快轮，也没有把 profile 的 20 样本与非 profile 的 50 样本合并。
- 根代理暂停重负载验证，但 WSL/CPU 的其他活动没有系统性控制。CPU/磁盘计数不是 PostgreSQL 服务端性能；以下数字不是生产 TTFB、模型首 token、HTTP/SSE 或浏览器绘制时间。

## 重复回归

顺序：bf 第1轮 → ff 第1轮 → ff 第2轮 → bf 第2轮。

| 来源 | admission→首持久 delta P50 / P95 ms | emit→首持久 delta P50 / P95 ms | 显式 SQL |
| --- | --- | --- | --- |
| bf 第1轮 | 24.948 / 40.921 | 2.316 / 4.489 | 5,395 |
| ff 第1轮 | 30.762 / 43.468 | 4.074 / 6.160 | 4,315 |
| ff 第2轮 | 41.171 / 57.408 | 5.244 / 8.243 | 4,377 |
| bf 第2轮 | 27.368 / 45.187 | 2.579 / 4.215 | 5,417 |

各来源合并原始 100 样本重新计算分位：bf admission 26.379 / 43.998ms、emit 2.409 / 4.369ms；ff admission 35.410 / 55.304ms、emit 4.649 / 7.308ms。不是对两轮分位取平均。

## 五个可证伪假设

| 假设 | 插桩和结果 | 处理 |
| --- | --- | --- |
| 文本提交前有重复 lease 读取 | ff profile 中每个首文本先完整 owns_lease 查询，再由 append_events 在 FOR UPDATE 行锁内检查相同 live owner/status/expiry。首 delta 前两次 callback lease 边界共40次，优化后只有 message.start 的20次。 | 仅去掉 text_delta 的独立 preflight；事务检查保留。 |
| 线程切换是主要成本 | ff profile 的 complete repository worker 与 async 边界差值通常约0.2–0.3ms，独立 lease 边界约1.0–1.4ms。 | 不回退为阻塞事件循环的同步 SQL，不削弱取消等待事务结果。 |
| context 准备重复读取变多 | bf 首 delta 前每 turn 的 run_context 约7次；ff 为1次，并在一个 complete worker 内准备。 | 该假设被否定，不修改 context。 |
| scheduler/后台查询竞争造成排队 | ff schedule 为非阻塞 hint；执行前仍有 claim 排队。全链路及 profile 后测出现明显 host noise，例如优化单轮最大 admission115.287ms。 | 不能用全链路单轮差值证明因果。保持现有 scheduler，与持久 admission gate 整合后单列复测。 |
| DeltaEventWriter 的 timer/锁延迟首 delta | 首帧立即 await persist；100ms timer 只作用于后续帧。bf 和 ff 都单独提交 message.start；修复不移动首帧计时点。 | 无100ms首帧等待证据，不改首帧提交/排序规则。 |

Profile 为可选 `--profile-first-delta`，记录 method/worker/SQL 起点与耗时，不记录 SQL 参数、结果或真实用户信息。每样本 trace 有4000条上限。profile 性能数字本身包含插桩及宿主噪声，不能当作非 profile 基线。

## 最小修复与复测

`AgentProgressWriter.on_event` 对 text_delta 直接进入现有 DeltaEventWriter 和 `append_events(..., lease_owner=instance_id)`。文本不涉及用量/工具副作用；每次首帧/缓冲 flush 的真实提交仍在事务内校验用户、活动状态、租约 owner 和未过期时间。其它事件、用量记录和终端路径保持原来的 lease 检查。缓冲仍为100ms / 4KiB。

修复比较顺序：ff 对照第1轮 → 优化第1轮 → 优化第2轮 → ff 对照第2轮。

| 来源 | admission→首持久 delta P50 / P95 ms | emit→首持久 delta P50 / P95 ms | 显式 SQL |
| --- | --- | --- | --- |
| ff 对照第1轮 | 33.190 / 46.538 | 4.659 / 6.047 | 4,342 |
| 优化第1轮 | 27.026 / 37.200 | 1.879 / 3.302 | 3,733 |
| 优化第2轮 | 30.673 / 51.893 | 2.677 / 5.111 | 3,842 |
| ff 对照第2轮 | 28.447 / 48.467 | 3.512 / 6.122 | 4,270 |

合并各自100个原始样本，admission P50/P95 从30.934/48.434到28.377/42.941ms；emit→commit 从4.046/6.121到2.265/4.639ms。优化第2轮的 admission P95 高于其对照，不能声称每轮全面改善。可以精确归因的是文本路径减少一个 lease SELECT 与一次 complete repository worker dispatch，真实持久提交边界不变。

四轮均每轮50次 completed，事件计数均为 message.start50、message.delta100、message.end50、run.completed50。后续7个文本 delta 合并成第2条，所有文本保留。

## 持久 admission gate：同源开关复测

测量于2026-10-10 12:38–12:39 UTC完成。运行时、receiver 和资格检查接线已冻结，调用者暂停重负载 CI/构建；仍不能控制其它宿主活动。四轮分别冻结同一份工作区 app，顺序为关闭第1轮 → 开启第1轮 → 开启第2轮 → 关闭第2轮，每轮50个完整 turn。四份 JSON 的逐文件摘要、完整 app 树摘要和 harness 摘要一致：

- 测量时 HEAD：`eb2b37b5098b9122480b4dc0ccdea7e7f4417049`；工作区包含未提交的 admission 增量，HEAD 不是该快照的完整内容标识。
- app 树 SHA256：`287c59ee5a5c923cc4d0ffe3a3b69f713dc3fb1ad3a4f9132f88679264c3c8a8`。
- harness SHA256：`e03c0253a1d7363355d77bd85640e3752731767609383d02065256bd0683f010`。
- `parameters.admission_enabled` 按顺序为 false / true / true / false；不开 profile。全部使用同一 PostgreSQL17.6、5ms 合成 Pi、pool10 / global4 / user2 和阶段60s / 进程90s期限。

开启组通过显式临时 schema migration 安装实际的 admission 组件，并调用实际 `DurableAdmissionGate.configure()`，绑定合成 `staging` / `pskit-agent-staging` 和64个零的 qualification SHA，再将同一 gate 传给真实 PersistentConversationStore、ComputeJobs、ComputeLeases。安装/配置在计时外完成；请求接纳与执行路径中的检查在计时内。关闭组是同源本地性能对照，不适合作为绕过发布隔离语义的运行方案。

| 同源开关 | admission→首持久 delta P50 / P95 ms | emit→首持久 delta P50 / P95 ms | 显式 SQL |
| --- | --- | --- | --- |
| 关闭第1轮 | 34.495 / 73.489 | 2.825 / 5.865 | 3,896 |
| 开启第1轮 | 40.665 / 62.576 | 2.976 / 4.950 | 4,593 |
| 开启第2轮 | 37.103 / 50.130 | 2.859 / 4.595 | 4,459 |
| 关闭第2轮 | 38.486 / 54.706 | 2.971 / 4.798 | 3,904 |

按开关合并各自100个原始样本重新计算分位：

| 边界 | 关闭 P50 / P95 ms | 开启 P50 / P95 ms |
| --- | --- | --- |
| admission→首持久 delta | 35.476 / 60.158 | 39.446 / 59.461 |
| emit→首持久 delta | 2.898 / 5.431 | 2.900 / 4.828 |

本次接纳到提交的中位时间增加3.971ms。开启组的 P95 略低，但关闭第1轮有112.440ms的最大值，不能据此声称 gate 改善尾延迟。文本 emit→commit 的中位时间基本相同；不再将新 gate 的必要查询归因于已移除的重复 lease preflight。四轮 SQL 数包含 scheduler 查询和显式锁查询，也不代表服务端全部工作量。

200/200 turn 完成。每轮均保留50条 message.start、100条合并 message.delta、50条 message.end、50条 run.completed，SQL失败为0，完成后 session lock 保留数为0。四个随机 schema 均已通过精确名称查询确认删除，所有原始样本保留。

测量完成后，整合方继续加固私有 release proof、管理员拒绝路径及历史数据库迁移：核心迁移不再自动安装准入组件，迁移切换与失败清理保留意外出现的闭锁记录。因此此树摘要只定位本次测量快照；不能把它标记为最终整合树或声称完整 CI 已覆盖后续增量。benchmark 本身已经显式安装组件；最终源码另做一个样本的安装/对话 smoke，不并入上述性能样本。最终整合 CI 单独执行。此测量没有覆盖真实 provider/Pi RPC、HTTP/SSE、浏览器、工具执行或真实 Staging 的发布 RPO/RTO。

## 验证与回滚

- 新 narrow unit 先红：3失败/3通过，失败精确展示额外 owns_lease；修复后6/6通过。
- benchmark 6个纯合成测试 + progress6 + delta7 + async cancellation2：21通过，0.65s。
- 随后新增 admission opt-in 绑定的2个参数化用例，benchmark 脚本自身8/8通过，0.10s；不把这次结果描述为上述全部单元测试再次重跑。
- 真实 PG 新增4个 progress fence 用例（取消、过期、改 owner、同 worker 的错误用户）+既有 batch/sequence/fairness6个：10通过，2.46s，0 skip。真实提交观察 `[True, False, False]`，只有首个合法文本持久化，迟到首帧和缓冲不发布。
- 上述修改文件 ruff 通过。没有声称完整 backend/frontend/deployment CI 已针对本增量重跑。
- 回滚只需还原 `agent_events.py` 的 text_delta 快路径；不需要数据迁移、删事件或账本调整。benchmark/profile/raw evidence 不改变应用行为。
- durable admission/release gate 的同源开关测量如上；早期优化快照与该测量快照不同，不能直接混为同一份基线。真实 Staging/provider/RPO/RTO 仍独立待验收。

## 原始文件与重跑

所有15份原始 JSON 位于 [raw-first-delta](raw-first-delta)，包括8轮早期非 profile、bf/ff/优化3份 profile、4轮同源 gate 开关。均包含 PG版本、可见 CPU、Python/psycopg、UTC时间、阶段期限、样本数、源 SHA、SQL分类、事件计数和全部 raw latency samples。早期八轮及 bf/ff profile 没有 harness SHA 字段；最后优化 profile 开始新增该字段，不回填假历史值。四份 gate 报告另含明确开关和完整 app 树摘要。

先由调用者设置本地合成 `TEST_POSTGRES_DSN`（不能指向真实库），随后运行：

```bash
new_backend/.venv/bin/python scripts/benchmark_new_stack.py \
  --source-ref ff873b20cdc5ecd81149f2e63542b76a32537c89 \
  --stages first_delta --samples 50 --stage-timeout 60 \
  --output /tmp/pskit-first-delta-diagnosis/control.json
```

bf对照替换 `--source-ref`；优化快照不传 `--source-ref`，必须先核对工作区 app diff/ownership。profile 增加 `--profile-first-delta --samples 20`，保持独立文件。实际执行每个进程外包 `timeout 90s`，所用 DSN 不写进报告。

同源 gate 开关复测不传 `--source-ref`：在冻结的工作区连续执行关闭/开启/开启/关闭四轮，开启轮增加 `--admission-enabled`，每轮使用独立输出文件。必须核对四轮 `app_tree_sha256`、`harness_source_sha256` 和逐文件 SHA 一致，并保留全部轮次；关闭轮省略该开关。
