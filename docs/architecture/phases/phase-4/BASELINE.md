# Phase 4 — 隔离运行时性能基线

状态：本地基线已记录；1000 Run 完整排空在180s窗口未通过，保留有界观察。没有修改应用运行时、生产配置或真实数据库，也没有声称优化后性能。

## 测量边界

- 测量真实 `AgentService.start()/schedule()/_scheduler_loop()/_execute()`、`PersistentConversationStore`、`PostgresStatements`、psycopg 与 PostgreSQL 17。插桩只统计驱动的执行/取行，不替换 SQL、结果或事务。
- Pi 是只产生规定回调的合成替身，每次 turn 先等待 5ms。它不连接供应商、不执行 Pi JSON RPC、不写 transcript、不调用 MCP/GPU。首增量数字只代表本地 admission、排队、事件提交开销，不能当作公网 TTFB、模型首 token 或浏览器绘制性能。
- 使用既有本地 `pskit-remediation-pg17-20261010` 容器，`127.0.0.1:32768/pskit_remediation`；每阶段创建随机 `pskit_benchmark_<uuid>` schema。仅迁移/清理该 schema，不重建容器、不删库、不读取 `.env`。连接拒绝非 loopback、真实数据库名、libpq service/passfile/证书路径；固定 PG17、连接 5s、SQL 5s、锁 3s 的超时。
- 应用 `new_backend/app` 在开始前复制到临时目录并冻结，避免其他阶段的并发编辑混入基线。临时 HOME、cache、cwd 和最小 mock 环境独立于已有配置。使用已安装的 Python 依赖，不下载依赖。
- 原运行时文件的 SHA256 与实际 HEAD 写入结果；HEAD 包含 Phase 2 的 CI/生成物/窄数据导出修复，运行时仍为 Phase 4 优化前的代码。

## 指标含义

| 指标 | 精确定义 |
| --- | --- |
| SQL 条数 | 驱动 `Cursor.execute()` 的显式 SQL；不包含 psycopg 隐式 BEGIN/COMMIT，因此不是完整网络 round trip 数。SELECT 包括 advisory lock。 |
| 写语句/受影响行 | INSERT/UPDATE/DELETE 执行次数与其实际 affected rows 分别统计；空表 recovery UPDATE 可能执行但修改 0 行。 |
| 物化行数 | 游标 fetchone/fetchall/iteration 真正消费的行；不保留行内容或参数。 |
| 首个持久增量 | `append_event(message.delta)` 的事务返回后记录；从 admission 开始及合成 Pi 首次 emit 分别计时。 |
| 排队首增量 | 队列构造完成、scheduler 启动时至首次持久增量；队列构造时间单列，不并入此分位。 |
| P50/P95 | 排序后 `(N-1)*q` 的线性插值；原样记录样本数，不用小样本冒充大样本。 |
| 调度/公平性 | 全局 4、单用户 2；正常吞吐为 10 用户 round-robin；另测 Alice 占满自己的 2 槽、Bob 在 99/999 个 Alice queued Run 后的尾部场景。 |
| 有界观察 | 若 1000 Run 无法在阶段窗口排空，输出 `bounded_observation`、已完成数量与观测窗口，不伪称全部通过。公平性允许优化后的 cursor 通过多次有界调用抵达尾部。 |

## 重跑命令

仓库根目录；`TEST_POSTGRES_DSN` 指向上面的合成本地库，不指向任何云端库：

```bash
new_backend/.venv/bin/python scripts/benchmark_new_stack.py \
  --source-ref bf3abbc7abcbfdb14f198965fa5d5dd7e6cc2d92 \
  --output /tmp/pskit-phase4-baseline.json
```

默认包含 300s idle、100/1000 Run 的公平性和排队、50 次首增量样本、单 turn 256 delta。普通阶段期限 180s；长队列在期限前 10s 结束有界观察。单独续测可用 `--stages queue first_delta delta_writes`；短 idle 只用于 harness smoke，`five_minute_gate=false`，不能代替 5 分钟结果。

共享本机 PG/WSL 的其他合成测试和 CPU/网络活动未受控制。结果用于同一机器、同一 harness 的前后比较，不代表生产多用户容量。尚未测量 PostgreSQL 服务端 CPU、真实供应商、HTTP/SSE/浏览器链路或跨主机可靠性。

## 结果

测量时间：2026-10-10，首段始于 07:48:34 UTC（15:48:34 CST）。WSL2 Linux 6.18.33.2、Python 3.12.9、psycopg 3.3.6，12 个可见 CPU；PostgreSQL 17.6 / schema version 13。

| 场景 | 实际结果 | 显式 SQL / SELECT / 写语句 | 物化行 / 事件写入 | 首持久增量 P50 / P95 |
| --- | --- | --- | --- | --- |
| 空闲 5 分钟 | 300.005s；103.088 SQL/s；无业务数据 | 30,927 / 30,636 / 291 | 10,018 行（主要 advisory 返回行）；业务写入0行 | 不适用 |
| 100 队列公平性 | Alice 占满2槽，Bob在99个Alice Run后仍被领取；160.801ms | 402 / 401 / 1 | 500 行；一次候选 SELECT 物化100行 | 不适用 |
| 1000 队列公平性 | Bob在999个Alice Run后仍被领取；1,564.160ms | 4,002 / 4,001 / 1 | 5,000 行；一次候选 SELECT 物化1000行 | 不适用 |
| 100正常排队 | 全部完成，5.024s，19.906 Run/s | 10,920 / 9,811 / 1,109 | 10,596 行；400个事件 | 2,947.194 / 4,740.827 ms（100样本） |
| 1000完整排空 | 原180s期限触发 TimeoutError | 该失败阶段未保留部分计数，不填造数据 | 未完成；完整分位未知 | **pending** |
| 1000有界观察 | 50.777s完成132/1000，2.600 Run/s | 134,676 / 133,149 / 1,527 | 164,164 行；528个事件（132个delta） | 26,090.223 / 47,957.624 ms（仅已输出的132样本） |
| 50次立即admission | 每turn8个delta，全部完成，2.747s | 5,407 / 4,251 / 1,156 | 4,148 行；550个事件 | 24.896 / 28.153 ms（50样本） |
| 单turn256delta | 完成，0.742s | 1,105 / 832 / 273 | 1,323 行；259个事件（256个delta） | 单样本不用于性能分位比较 |

5分钟 idle 的 benchmark 进程 user CPU 17.401046s、system CPU 5.857239s；只测量本进程，**不代表 PostgreSQL 服务端 CPU**。291个空表 recovery UPDATE 的 affected rows 均为0。没有模型/GPU/HTTP业务请求。

50次立即admission中，合成 Pi emit 到首次事务返回的 P50/P95 为 **2.105 / 2.443 ms**；前述24.896/28.153ms包含真实Session创建、admission及立即schedule路径。256delta事件分布为 `message.start=1, message.delta=256, message.end=1, run.completed=1`，没有用假打字机代替事件写入。

会话锁在turn结束后仍保留：100正常排队结束为100个、50个立即turn结束为50个；1000有界观察已启动136个turn，停止收尾后仍保留136个。该数据确认锁字典没有随turn完成回收，尚未修复。

### 解释与未完成目标

1. 公平性场景**没有确认尾部用户饥饿**；已确认现实现一次物化全部100/1000候选，并逐个做领取检查，SQL数从402增长到4002。优化必须保留尾部用户最终能取得槽位，不能用裸LIMIT剪掉Bob。
2. 100→1000正常队列在有限观测内吞吐下降；同机共享CPU/PG存在噪声，因此这些数字是基准，尚不是“上线会快多少”的预测。134,676条SQL/164,164物化行足以证明客户端全候选尝试的实际成本，不能仅凭类的行数声称优化。
3. 1000有界观察的132个首增量样本**不是1000整体P95**，不能与优化后1000全部样本直接做同分布分位比较。后续可比较同期限的完成数/SQL/物化行，并复跑固定50个立即admission样本；完整1000排空和分位目标仍pending。
4. 首段使用冻结工作区快照，五个关键运行时文件SHA与`bf3abbc`完全一致。第一次续测发现Phase3已改变Agent环境/Profile参数，主动丢弃该续测作为正式基线。最终首增量/写入/1000有界队列从**只读Git `bf3abbc` app完整快照**重跑，并确认五个运行时SHA与首段一致；详细报告分别保留source类型，不隐瞒来源差别。
5. SQL计数包含插桩开销，隐式事务网络命令未计数；两边必须用同样的harness和明确source SHA比较。若事件合并新增批提交方法，持久delta计时插桩必须相应扩展到真实batch commit返回，不可改记emit来降低数字。

续测命令（未重复已完成的5分钟idle）：

```bash
new_backend/.venv/bin/python scripts/benchmark_new_stack.py \
  --source-ref bf3abbc7abcbfdb14f198965fa5d5dd7e6cc2d92 \
  --stages queue first_delta delta_writes --queue-sizes 1000 \
  --stage-timeout 60 --output /tmp/pskit-phase4-baseline-continuation.json
```

此续测报告的总体status为`partial`，1000队列为`bounded_observation`且`queue_drained=false`；命令完成不等于完整排空通过。两个正式报告、两份被丢弃的诊断报告及早期smoke均已收尾；只读查询确认残留`pskit_benchmark_%` schema为0。


## 验证与回滚

已完成短 smoke：idle 1s、4 Run 的公平性和排队、2 个首增量 turn、单 turn 256 delta 均走真实 PG 并完成；256 条 delta 对应 256 个 `message.delta` INSERT。六个不允许的 DSN 在连接前被拒绝；分位函数检查通过；最后固定 `bf3abbc` 的全阶段短 smoke、ruff/compile通过；malformed DSN 的CLI日志只输出异常类型，没有泄漏测试sentinel。早期 smoke 的游标 iteration 插桩递归问题只修改 harness 后重测，未修改应用代码。

本变更只有 benchmark 和报告；无需数据库迁移或部署。回退删除本地 benchmark/report 即可，不影响应用。每阶段的随机 schema 在 finally 中清理；中断后先核对结果中的具体 UUID，再只清理该 UUID，不能执行全库或全容器 prune。

## 原始计数证据

以下JSON不含DSN密码、SQL参数、行内容、模型凭据或私密输入；用于在临时文件清理后复核分位、计数与运行时SHA。

<details>
<summary>初次冻结工作区：300s idle/公平性/100队列，1000完整排空超时</summary>

```json
{
  "format": "pskit.new-stack-benchmark.v1",
  "status": "failed",
  "started_at": "2026-10-10T07:48:34.406000+00:00",
  "source_revision": "bf3abbc7abcbfdb14f198965fa5d5dd7e6cc2d92",
  "runtime_source_sha256": {
    "app/services/agent.py": "01cb66319e44491439c57afc2b310e5dfdfac29199f9fc057aac795bcee5fe5c",
    "app/db/postgres.py": "b9ebdeaae62d802a5e7097e1e5e2379e9357760ac3f1ccd58760ec5728b3bf5e",
    "app/domain/persistent_conversation/store.py": "e5a6d3aeb5d4c3acfef3689c98803ea32c890b753eb25b4a91ce2e77c7b88bac",
    "app/domain/persistent_conversation/runs.py": "5244db294d186ed4191abebb7d3b54adb21d10bcea8fe01bc4125f33c0d47023",
    "app/domain/persistent_conversation/recovery.py": "88e50b0b36c2da074cab5ed69a4e459aa1f7906ebb69a7ddbf8de33abd1998c3"
  },
  "source_snapshot": "only new_backend/app, frozen before measurements; no .env/data/credentials",
  "machine": {
    "platform": "Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.35",
    "python": "3.12.9",
    "cpu_count": 12,
    "psycopg": "3.3.6"
  },
  "postgres": {
    "server_version_num": 170006,
    "server_version": "17.6 (Debian 17.6-2.pgdg13+1)",
    "database": "pskit_remediation",
    "address": "127.0.0.1",
    "port": 32768
  },
  "parameters": {
    "idle_seconds": 300,
    "queue_sizes": [
      100,
      1000
    ],
    "first_delta_samples": 50,
    "stage_timeout_seconds": 180,
    "global_active": 4,
    "per_user_active": 2,
    "pool_max_size": 10,
    "statement_timeout_ms": 5000,
    "lock_timeout_ms": 3000
  },
  "counter_definition": "real driver Cursor.execute calls; explicit SQL only, not implicit BEGIN/COMMIT; SELECT includes admission advisory locks; write statements may affect zero rows; observer queries separately counted; no SQL parameters/results retained",
  "latency_definition": "local synthetic Pi delay 5ms + actual admission/scheduler/event commits; not provider latency, HTTP TTFB, network SSE, browser paint, or transcript write throughput",
  "concurrent_activity": "shared local PG17 may run other synthetic test schemas; host CPU/network noise uncontrolled",
  "cleanup": "each stage closes its pool and drops only its generated pskit_benchmark_<uuid> schema",
  "stages": {
    "idle": {
      "status": "passed",
      "schema": "pskit_benchmark_73b2f4aee2444b4f9bbc921a0f0bfbf1",
      "elapsed_seconds": 300.005,
      "five_minute_gate": true,
      "process_cpu_user_seconds": 17.401046,
      "process_cpu_system_seconds": 5.857239,
      "sql_statements_per_second": 103.088,
      "max_rss_kib_process": 827932,
      "measurement": {
        "sql_statements": 30927,
        "select_statements": 30636,
        "write_statements": 291,
        "observer_statements": 0,
        "affected_write_rows": {},
        "fetched_rows": 10018,
        "fetched_by_query": {
          "SELECT 169eb95272e4": 10018
        },
        "statements_by_query": {
          "SELECT 0f508de0ad3c": 291,
          "UPDATE 536592323c8d": 291,
          "SELECT cdbc855e1fb2": 291,
          "SELECT 169eb95272e4": 10018,
          "SELECT 473370567aec": 5009,
          "SELECT b58a30708eea": 5009,
          "SELECT wakeup_candidates": 5009,
          "SELECT queued_candidates": 5009
        },
        "event_inserts_by_type": {},
        "sql_execute_seconds_including_observer": 23.385043,
        "sql_failures": 0,
        "store_method_calls": {
          "recover_wakeups": 291,
          "renew_leases": 291,
          "expire_queued_compute_jobs": 5009,
          "expire_running_compute_jobs": 5009,
          "claim_wakeups": 5009,
          "claim_queued_runs": 5009
        }
      }
    },
    "fairness_100": {
      "status": "passed",
      "schema": "pskit_benchmark_c07277bd2b2441e98f7cfe8421c05cbb",
      "queued_runs": 100,
      "blocked_prefix": 99,
      "claimed_tail_user": true,
      "claim_elapsed_ms": 160.801,
      "measurement": {
        "sql_statements": 402,
        "select_statements": 401,
        "write_statements": 1,
        "observer_statements": 0,
        "affected_write_rows": {
          "UPDATE 1b9ab21d056c": 1
        },
        "fetched_rows": 500,
        "fetched_by_query": {
          "SELECT queued_candidates": 100,
          "SELECT 169eb95272e4": 100,
          "SELECT 985d9d83ef91": 100,
          "SELECT 18fde5ae6866": 100,
          "SELECT 90a8abaedbe2": 100
        },
        "statements_by_query": {
          "SELECT queued_candidates": 1,
          "SELECT 169eb95272e4": 100,
          "SELECT 985d9d83ef91": 100,
          "SELECT 18fde5ae6866": 100,
          "SELECT 90a8abaedbe2": 100,
          "UPDATE 1b9ab21d056c": 1
        },
        "event_inserts_by_type": {},
        "sql_execute_seconds_including_observer": 0.095903,
        "sql_failures": 0,
        "store_method_calls": {}
      }
    },
    "fairness_1000": {
      "status": "passed",
      "schema": "pskit_benchmark_ab86fe618a454632bb4615ae2e12195a",
      "queued_runs": 1000,
      "blocked_prefix": 999,
      "claimed_tail_user": true,
      "claim_elapsed_ms": 1564.16,
      "measurement": {
        "sql_statements": 4002,
        "select_statements": 4001,
        "write_statements": 1,
        "observer_statements": 0,
        "affected_write_rows": {
          "UPDATE 1b9ab21d056c": 1
        },
        "fetched_rows": 5000,
        "fetched_by_query": {
          "SELECT queued_candidates": 1000,
          "SELECT 169eb95272e4": 1000,
          "SELECT 985d9d83ef91": 1000,
          "SELECT 18fde5ae6866": 1000,
          "SELECT 90a8abaedbe2": 1000
        },
        "statements_by_query": {
          "SELECT queued_candidates": 1,
          "SELECT 169eb95272e4": 1000,
          "SELECT 985d9d83ef91": 1000,
          "SELECT 18fde5ae6866": 1000,
          "SELECT 90a8abaedbe2": 1000,
          "UPDATE 1b9ab21d056c": 1
        },
        "event_inserts_by_type": {},
        "sql_execute_seconds_including_observer": 0.934524,
        "sql_failures": 0,
        "store_method_calls": {}
      }
    },
    "queue_100": {
      "status": "passed",
      "schema": "pskit_benchmark_3ac945d4552d4defa0118ed05978deae",
      "queued_runs": 100,
      "owners": 10,
      "ingress_owner_order": "round-robin",
      "setup_seconds_excluded": 0.611,
      "elapsed_seconds": 5.024,
      "completed_per_second": 19.906,
      "queue_start_to_first_durable_delta": {
        "samples": 100,
        "p50_ms": 2947.194,
        "p95_ms": 4740.827,
        "max_ms": 4926.175
      },
      "started_turns_by_user": {
        "queue-user-0": 10,
        "queue-user-1": 10,
        "queue-user-2": 10,
        "queue-user-3": 10,
        "queue-user-4": 10,
        "queue-user-5": 10,
        "queue-user-6": 10,
        "queue-user-7": 10,
        "queue-user-8": 10,
        "queue-user-9": 10
      },
      "first_delta_ms_by_user": {
        "queue-user-0": 205.026,
        "queue-user-1": 234.974,
        "queue-user-2": 256.738,
        "queue-user-3": 277.714,
        "queue-user-4": 472.65,
        "queue-user-5": 492.214,
        "queue-user-6": 515.654,
        "queue-user-7": 539.435,
        "queue-user-8": 734.422,
        "queue-user-9": 753.733
      },
      "retained_session_locks_after_turns": 100,
      "measurement": {
        "sql_statements": 10920,
        "select_statements": 9811,
        "write_statements": 1109,
        "observer_statements": 28,
        "affected_write_rows": {
          "UPDATE 1b9ab21d056c": 100,
          "UPDATE c07703eecedb": 100,
          "INSERT agent_events": 400,
          "INSERT 29cf1e549bcc": 100,
          "INSERT 1c92855a9fa9": 100,
          "INSERT d0433d5f0167": 100,
          "UPDATE 725685af3527": 100
        },
        "fetched_rows": 10596,
        "fetched_by_query": {
          "observer SELECT f5b08ffec7a4": 2800,
          "SELECT 169eb95272e4": 1764,
          "SELECT queued_candidates": 1308,
          "SELECT 985d9d83ef91": 1308,
          "SELECT 18fde5ae6866": 1308,
          "SELECT 90a8abaedbe2": 1308,
          "SELECT 489016622ba3": 500,
          "SELECT edefb5f0880a": 800,
          "SELECT 1e1b2211298d": 600,
          "SELECT 3f67a0d7c2c2": 400,
          "SELECT b9799ed19a5f": 200,
          "SELECT ce3f2c95973a": 200,
          "SELECT f9945f0686bd": 200,
          "SELECT 23e825e5ee15": 100,
          "SELECT 4d1b32b59512": 600
        },
        "statements_by_query": {
          "SELECT cdbc855e1fb2": 6,
          "observer SELECT f5b08ffec7a4": 28,
          "SELECT 0f508de0ad3c": 5,
          "UPDATE 536592323c8d": 5,
          "SELECT 169eb95272e4": 1764,
          "SELECT 473370567aec": 28,
          "SELECT b58a30708eea": 28,
          "SELECT wakeup_candidates": 28,
          "SELECT queued_candidates": 28,
          "SELECT 985d9d83ef91": 1308,
          "SELECT 18fde5ae6866": 1308,
          "SELECT 90a8abaedbe2": 1308,
          "UPDATE 1b9ab21d056c": 100,
          "SELECT 489016622ba3": 500,
          "UPDATE c07703eecedb": 100,
          "SELECT edefb5f0880a": 800,
          "SELECT bdfeac34da37": 100,
          "SELECT fae2212804eb": 100,
          "SELECT 1e1b2211298d": 600,
          "SELECT 3f67a0d7c2c2": 400,
          "INSERT agent_events": 400,
          "SELECT b9799ed19a5f": 200,
          "SELECT ce3f2c95973a": 200,
          "INSERT 29cf1e549bcc": 100,
          "SELECT 3b84c2508228": 100,
          "SELECT f9945f0686bd": 200,
          "SELECT 2c1dee434727": 100,
          "SELECT 07688eae770a": 100,
          "SELECT 23e825e5ee15": 100,
          "INSERT 1c92855a9fa9": 100,
          "UPDATE 514991eb1480": 100,
          "SELECT 4d1b32b59512": 200,
          "SELECT 0f896edfdc31": 100,
          "SELECT 4c0d4534cb1d": 100,
          "SELECT 99c84deab261": 100,
          "INSERT d0433d5f0167": 100,
          "UPDATE 725685af3527": 100,
          "UPDATE c5a0fb97afbe": 4
        },
        "event_inserts_by_type": {
          "message.start": 100,
          "message.delta": 100,
          "message.end": 100,
          "run.completed": 100
        },
        "sql_execute_seconds_including_observer": 2.934421,
        "sql_failures": 0,
        "store_method_calls": {
          "recover_wakeups": 6,
          "renew_leases": 5,
          "expire_queued_compute_jobs": 28,
          "expire_running_compute_jobs": 28,
          "claim_wakeups": 28,
          "claim_queued_runs": 28
        }
      }
    },
    "queue_1000": {
      "status": "failed",
      "error_type": "TimeoutError"
    }
  }
}
```

</details>

<details>
<summary>精确bf3abbc续测：1000有界观察/50首增量/256delta</summary>

```json
{
  "format": "pskit.new-stack-benchmark.v1",
  "status": "partial",
  "started_at": "2026-10-10T07:59:48.009168+00:00",
  "source_revision": "bf3abbc7abcbfdb14f198965fa5d5dd7e6cc2d92",
  "runtime_source_sha256": {
    "app/services/agent.py": "01cb66319e44491439c57afc2b310e5dfdfac29199f9fc057aac795bcee5fe5c",
    "app/db/postgres.py": "b9ebdeaae62d802a5e7097e1e5e2379e9357760ac3f1ccd58760ec5728b3bf5e",
    "app/domain/persistent_conversation/store.py": "e5a6d3aeb5d4c3acfef3689c98803ea32c890b753eb25b4a91ce2e77c7b88bac",
    "app/domain/persistent_conversation/runs.py": "5244db294d186ed4191abebb7d3b54adb21d10bcea8fe01bc4125f33c0d47023",
    "app/domain/persistent_conversation/recovery.py": "88e50b0b36c2da074cab5ed69a4e459aa1f7906ebb69a7ddbf8de33abd1998c3"
  },
  "source_kind": "local-git-commit",
  "source_snapshot": "only new_backend/app, frozen before measurements; no .env/data/credentials",
  "machine": {
    "platform": "Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.35",
    "python": "3.12.9",
    "cpu_count": 12,
    "psycopg": "3.3.6"
  },
  "postgres": {
    "server_version_num": 170006,
    "server_version": "17.6 (Debian 17.6-2.pgdg13+1)",
    "database": "pskit_remediation",
    "address": "127.0.0.1",
    "port": 32768
  },
  "parameters": {
    "idle_seconds": 300,
    "queue_sizes": [
      1000
    ],
    "first_delta_samples": 50,
    "stages": [
      "queue",
      "first_delta",
      "delta_writes"
    ],
    "stage_timeout_seconds": 60.0,
    "global_active": 4,
    "per_user_active": 2,
    "pool_max_size": 10,
    "statement_timeout_ms": 5000,
    "lock_timeout_ms": 3000
  },
  "counter_definition": "real driver Cursor.execute calls; explicit SQL only, not implicit BEGIN/COMMIT; SELECT includes admission advisory locks; write statements may affect zero rows; observer queries separately counted; no SQL parameters/results retained",
  "latency_definition": "local synthetic Pi delay 5ms + actual admission/scheduler/event commits; not provider latency, HTTP TTFB, network SSE, browser paint, or transcript write throughput",
  "concurrent_activity": "shared local PG17 may run other synthetic test schemas; host CPU/network noise uncontrolled",
  "cleanup": "each stage closes its pool and drops only its generated pskit_benchmark_<uuid> schema",
  "stages": {
    "queue_1000": {
      "status": "bounded_observation",
      "schema": "pskit_benchmark_094ed52798754240a9249d5149fa6e87",
      "queued_runs": 1000,
      "queue_drained": false,
      "completed_runs": 132,
      "first_delta_runs": 132,
      "owners": 10,
      "ingress_owner_order": "round-robin",
      "setup_seconds_excluded": 5.396,
      "elapsed_seconds": 50.777,
      "completed_per_second": 2.6,
      "queue_start_to_first_durable_delta": {
        "samples": 132,
        "p50_ms": 26090.223,
        "p95_ms": 47957.624,
        "max_ms": 49497.193
      },
      "started_turns_by_user": {
        "queue-user-0": 14,
        "queue-user-1": 14,
        "queue-user-2": 14,
        "queue-user-3": 14,
        "queue-user-4": 14,
        "queue-user-5": 14,
        "queue-user-6": 13,
        "queue-user-7": 13,
        "queue-user-8": 13,
        "queue-user-9": 13
      },
      "first_delta_ms_by_user": {
        "queue-user-0": 1473.66,
        "queue-user-1": 1500.034,
        "queue-user-2": 1520.849,
        "queue-user-3": 1542.632,
        "queue-user-4": 3104.825,
        "queue-user-5": 3124.599,
        "queue-user-6": 3147.824,
        "queue-user-7": 3167.669,
        "queue-user-8": 4711.179,
        "queue-user-9": 4730.278
      },
      "retained_session_locks_after_turns": 136,
      "measurement": {
        "sql_statements": 134676,
        "select_statements": 133149,
        "write_statements": 1527,
        "observer_statements": 35,
        "affected_write_rows": {
          "UPDATE 1b9ab21d056c": 136,
          "UPDATE c07703eecedb": 136,
          "INSERT agent_events": 528,
          "INSERT 29cf1e549bcc": 132,
          "INSERT 1c92855a9fa9": 132,
          "INSERT d0433d5f0167": 132,
          "UPDATE 725685af3527": 132
        },
        "fetched_rows": 164164,
        "fetched_by_query": {
          "observer SELECT f5b08ffec7a4": 35000,
          "SELECT 169eb95272e4": 32356,
          "SELECT queued_candidates": 31756,
          "SELECT 985d9d83ef91": 31756,
          "SELECT 18fde5ae6866": 31756,
          "SELECT 90a8abaedbe2": 31756,
          "SELECT 489016622ba3": 664,
          "SELECT edefb5f0880a": 1084,
          "SELECT 1e1b2211298d": 792,
          "SELECT 3f67a0d7c2c2": 528,
          "SELECT b9799ed19a5f": 264,
          "SELECT ce3f2c95973a": 264,
          "SELECT f9945f0686bd": 264,
          "SELECT 23e825e5ee15": 132,
          "SELECT 4d1b32b59512": 792
        },
        "statements_by_query": {
          "SELECT cdbc855e1fb2": 35,
          "observer SELECT f5b08ffec7a4": 35,
          "SELECT 0f508de0ad3c": 34,
          "UPDATE 536592323c8d": 34,
          "SELECT 169eb95272e4": 32356,
          "SELECT 473370567aec": 34,
          "SELECT b58a30708eea": 34,
          "SELECT wakeup_candidates": 34,
          "SELECT queued_candidates": 34,
          "SELECT 985d9d83ef91": 31756,
          "SELECT 18fde5ae6866": 31756,
          "SELECT 90a8abaedbe2": 31756,
          "UPDATE 1b9ab21d056c": 136,
          "SELECT 489016622ba3": 664,
          "UPDATE c07703eecedb": 136,
          "SELECT edefb5f0880a": 1084,
          "SELECT bdfeac34da37": 136,
          "SELECT fae2212804eb": 136,
          "SELECT 1e1b2211298d": 792,
          "SELECT 3f67a0d7c2c2": 528,
          "INSERT agent_events": 528,
          "SELECT b9799ed19a5f": 264,
          "SELECT ce3f2c95973a": 264,
          "INSERT 29cf1e549bcc": 132,
          "SELECT 3b84c2508228": 132,
          "SELECT f9945f0686bd": 264,
          "SELECT 2c1dee434727": 132,
          "SELECT 07688eae770a": 132,
          "SELECT 23e825e5ee15": 132,
          "INSERT 1c92855a9fa9": 132,
          "UPDATE 514991eb1480": 132,
          "SELECT 4d1b32b59512": 264,
          "SELECT 0f896edfdc31": 132,
          "SELECT 4c0d4534cb1d": 132,
          "SELECT 99c84deab261": 132,
          "INSERT d0433d5f0167": 132,
          "UPDATE 725685af3527": 132,
          "UPDATE c5a0fb97afbe": 33
        },
        "event_inserts_by_type": {
          "message.start": 132,
          "message.delta": 132,
          "message.end": 132,
          "run.completed": 132
        },
        "sql_execute_seconds_including_observer": 30.301401,
        "sql_failures": 0,
        "store_method_calls": {
          "recover_wakeups": 35,
          "renew_leases": 34,
          "expire_queued_compute_jobs": 34,
          "expire_running_compute_jobs": 34,
          "claim_wakeups": 34,
          "claim_queued_runs": 34
        }
      }
    },
    "first_delta": {
      "status": "passed",
      "name": "first_delta",
      "schema": "pskit_benchmark_1852f48af685451ea2d3e9c5099690e6",
      "elapsed_seconds": 2.747,
      "samples": 50,
      "synthetic_deltas_per_turn": 8,
      "synthetic_pi_delay_ms": 5,
      "admission_to_first_durable_delta": {
        "samples": 50,
        "p50_ms": 24.896,
        "p95_ms": 28.153,
        "max_ms": 39.268
      },
      "emission_to_first_durable_delta": {
        "samples": 50,
        "p50_ms": 2.105,
        "p95_ms": 2.443,
        "max_ms": 2.733
      },
      "retained_session_locks_after_turns": 50,
      "measurement": {
        "sql_statements": 5407,
        "select_statements": 4251,
        "write_statements": 1156,
        "observer_statements": 50,
        "affected_write_rows": {
          "INSERT f1a9db7e9769": 50,
          "INSERT c2ad233312de": 50,
          "INSERT d0433d5f0167": 100,
          "INSERT 4b58693227ed": 50,
          "INSERT 29cf1e549bcc": 100,
          "UPDATE 1b9ab21d056c": 50,
          "UPDATE c5a0fb97afbe": 3,
          "UPDATE c07703eecedb": 50,
          "INSERT agent_events": 550,
          "INSERT 1c92855a9fa9": 50,
          "UPDATE 725685af3527": 50
        },
        "fetched_rows": 4148,
        "fetched_by_query": {
          "SELECT 169eb95272e4": 398,
          "SELECT 3cfdb680a3b0": 50,
          "SELECT 985d9d83ef91": 50,
          "SELECT 18fde5ae6866": 50,
          "SELECT 90a8abaedbe2": 50,
          "SELECT 489016622ba3": 600,
          "SELECT edefb5f0880a": 400,
          "SELECT 1e1b2211298d": 650,
          "SELECT 3f67a0d7c2c2": 550,
          "SELECT b9799ed19a5f": 100,
          "SELECT ce3f2c95973a": 100,
          "SELECT f9945f0686bd": 100,
          "SELECT 23e825e5ee15": 50,
          "SELECT 4d1b32b59512": 1000,
          "observer SELECT 89534a623bbc": 50
        },
        "statements_by_query": {
          "SELECT cdbc855e1fb2": 4,
          "SELECT 86f67e3420c7": 100,
          "SELECT b600b7f026a9": 100,
          "SELECT 4d8eb9873277": 100,
          "INSERT f1a9db7e9769": 50,
          "SELECT 169eb95272e4": 398,
          "SELECT 3cfdb680a3b0": 50,
          "SELECT 8c74bb85e2c4": 50,
          "SELECT 94456c448e1a": 50,
          "INSERT c2ad233312de": 50,
          "INSERT d0433d5f0167": 100,
          "INSERT 4b58693227ed": 50,
          "INSERT 29cf1e549bcc": 100,
          "SELECT 985d9d83ef91": 50,
          "SELECT 18fde5ae6866": 50,
          "SELECT 90a8abaedbe2": 50,
          "UPDATE 1b9ab21d056c": 50,
          "SELECT 0f508de0ad3c": 3,
          "UPDATE 536592323c8d": 3,
          "UPDATE c5a0fb97afbe": 3,
          "SELECT 473370567aec": 49,
          "SELECT b58a30708eea": 49,
          "SELECT wakeup_candidates": 49,
          "SELECT queued_candidates": 49,
          "SELECT 489016622ba3": 600,
          "UPDATE c07703eecedb": 50,
          "SELECT edefb5f0880a": 400,
          "SELECT bdfeac34da37": 50,
          "SELECT fae2212804eb": 50,
          "SELECT 1e1b2211298d": 650,
          "SELECT 3f67a0d7c2c2": 550,
          "INSERT agent_events": 550,
          "SELECT b9799ed19a5f": 100,
          "SELECT ce3f2c95973a": 100,
          "SELECT 3b84c2508228": 50,
          "SELECT f9945f0686bd": 100,
          "SELECT 2c1dee434727": 50,
          "SELECT 07688eae770a": 50,
          "SELECT 23e825e5ee15": 50,
          "INSERT 1c92855a9fa9": 50,
          "UPDATE 514991eb1480": 50,
          "SELECT 4d1b32b59512": 100,
          "SELECT 0f896edfdc31": 50,
          "SELECT 4c0d4534cb1d": 50,
          "SELECT 99c84deab261": 50,
          "UPDATE 725685af3527": 50,
          "observer SELECT 89534a623bbc": 50
        },
        "event_inserts_by_type": {
          "message.start": 50,
          "message.delta": 400,
          "message.end": 50,
          "run.completed": 50
        },
        "sql_execute_seconds_including_observer": 1.458144,
        "sql_failures": 0,
        "store_method_calls": {
          "recover_wakeups": 4,
          "renew_leases": 3,
          "expire_queued_compute_jobs": 49,
          "expire_running_compute_jobs": 49,
          "claim_wakeups": 49,
          "claim_queued_runs": 49
        }
      }
    },
    "delta_writes": {
      "status": "passed",
      "name": "delta_writes",
      "schema": "pskit_benchmark_fe1fc6c7b6fe4c2fb972560bf33b3331",
      "elapsed_seconds": 0.742,
      "samples": 1,
      "synthetic_deltas_per_turn": 256,
      "synthetic_pi_delay_ms": 5,
      "admission_to_first_durable_delta": {
        "samples": 1,
        "p50_ms": 40.971,
        "p95_ms": 40.971,
        "max_ms": 40.971
      },
      "emission_to_first_durable_delta": {
        "samples": 1,
        "p50_ms": 3.409,
        "p95_ms": 3.409,
        "max_ms": 3.409
      },
      "retained_session_locks_after_turns": 1,
      "measurement": {
        "sql_statements": 1105,
        "select_statements": 832,
        "write_statements": 273,
        "observer_statements": 1,
        "affected_write_rows": {
          "INSERT f1a9db7e9769": 1,
          "INSERT c2ad233312de": 1,
          "INSERT d0433d5f0167": 2,
          "INSERT 4b58693227ed": 1,
          "INSERT 29cf1e549bcc": 2,
          "UPDATE 1b9ab21d056c": 1,
          "UPDATE c5a0fb97afbe": 1,
          "UPDATE c07703eecedb": 1,
          "INSERT agent_events": 259,
          "INSERT 1c92855a9fa9": 1,
          "UPDATE 725685af3527": 1
        },
        "fetched_rows": 1323,
        "fetched_by_query": {
          "SELECT 169eb95272e4": 8,
          "SELECT 3cfdb680a3b0": 1,
          "SELECT 985d9d83ef91": 1,
          "SELECT 18fde5ae6866": 1,
          "SELECT 90a8abaedbe2": 1,
          "SELECT 489016622ba3": 260,
          "SELECT edefb5f0880a": 8,
          "SELECT 1e1b2211298d": 261,
          "SELECT 3f67a0d7c2c2": 259,
          "SELECT b9799ed19a5f": 2,
          "SELECT ce3f2c95973a": 2,
          "SELECT f9945f0686bd": 2,
          "SELECT 23e825e5ee15": 1,
          "SELECT 4d1b32b59512": 516,
          "observer SELECT 89534a623bbc": 1
        },
        "statements_by_query": {
          "SELECT cdbc855e1fb2": 2,
          "SELECT 86f67e3420c7": 2,
          "SELECT b600b7f026a9": 2,
          "SELECT 4d8eb9873277": 2,
          "INSERT f1a9db7e9769": 1,
          "SELECT 169eb95272e4": 8,
          "SELECT 3cfdb680a3b0": 1,
          "SELECT 8c74bb85e2c4": 1,
          "SELECT 94456c448e1a": 1,
          "INSERT c2ad233312de": 1,
          "INSERT d0433d5f0167": 2,
          "INSERT 4b58693227ed": 1,
          "INSERT 29cf1e549bcc": 2,
          "SELECT 985d9d83ef91": 1,
          "SELECT 18fde5ae6866": 1,
          "SELECT 90a8abaedbe2": 1,
          "UPDATE 1b9ab21d056c": 1,
          "SELECT 0f508de0ad3c": 1,
          "UPDATE 536592323c8d": 1,
          "UPDATE c5a0fb97afbe": 1,
          "SELECT 473370567aec": 1,
          "SELECT b58a30708eea": 1,
          "SELECT wakeup_candidates": 1,
          "SELECT queued_candidates": 1,
          "SELECT 489016622ba3": 260,
          "UPDATE c07703eecedb": 1,
          "SELECT edefb5f0880a": 8,
          "SELECT bdfeac34da37": 1,
          "SELECT fae2212804eb": 1,
          "SELECT 1e1b2211298d": 261,
          "SELECT 3f67a0d7c2c2": 259,
          "INSERT agent_events": 259,
          "SELECT b9799ed19a5f": 2,
          "SELECT ce3f2c95973a": 2,
          "SELECT 3b84c2508228": 1,
          "SELECT f9945f0686bd": 2,
          "SELECT 2c1dee434727": 1,
          "SELECT 07688eae770a": 1,
          "SELECT 23e825e5ee15": 1,
          "INSERT 1c92855a9fa9": 1,
          "UPDATE 514991eb1480": 1,
          "SELECT 4d1b32b59512": 2,
          "SELECT 0f896edfdc31": 1,
          "SELECT 4c0d4534cb1d": 1,
          "SELECT 99c84deab261": 1,
          "UPDATE 725685af3527": 1,
          "observer SELECT 89534a623bbc": 1
        },
        "event_inserts_by_type": {
          "message.start": 1,
          "message.delta": 256,
          "message.end": 1,
          "run.completed": 1
        },
        "sql_execute_seconds_including_observer": 0.391601,
        "sql_failures": 0,
        "store_method_calls": {
          "recover_wakeups": 2,
          "renew_leases": 1,
          "expire_queued_compute_jobs": 1,
          "expire_running_compute_jobs": 1,
          "claim_wakeups": 1,
          "claim_queued_runs": 1
        }
      }
    }
  },
  "finished_at": "2026-10-10T08:00:52.328184+00:00"
}
```

</details>
