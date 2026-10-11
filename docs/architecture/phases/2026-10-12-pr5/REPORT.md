# PR-5：新 Run 只暴露业务计算工具，历史通用调用保留

## 根因与兼容方案

`extension.js` 原来只要能力目录非空就注册 `submit_compute`，同时已有绑定不可变 release/action 的业务工具。因此新 Run 仍让模型选择通用 capability/version/budget。内部计算端点、历史 transcript 和异步恢复不能被直接删除。

公开消息准入在既有 `context_json` 中固定 `compute_tool_compatibility`，默认 false；`RESEARCH_AGENT_COMPUTE_TOOL_COMPATIBILITY_ENABLED=true` 是仅由服务器设置的显式兼容入口，只影响后来新建的 Run。幂等重试返回原 Run/策略，不跟随此后的设置变化。历史 context 缺该字段仍按 true 恢复；不会回填、改写会话或迁移数据库。

Agent 生成 Run 专属 `PSKIT_COMPUTE_COMPAT_ENABLED=0/1`，Sandbox allowlist 保留它。Pi 仅在精确 `1` 时注册通用工具；业务工具、AF3、`pskit_resume` 和旧 `/internal/compute/jobs` 端点保持。新模式仍能接收历史 Job 的实际 terminal result/usage 并触发继续对话；没有把失败结果当成功。

业务入口仍在服务器根据 immutable release/action、用户可见性及固定预算执行，不接受模型替换 release/version/budget；已有 PostgreSQL Tool Product 和 ledger 回归负责此边界。通用入口只保留原兼容行为，不宣称已经完成使用量审计或将来删除它。

## 修改文件

- `apps/api/app/config.py`、`api/workspace.py`：服务器设置与准入接线，无新公开请求字段。
- `domain/conversation.py`、`domain/persistent_conversation/runs.py`：mock 兼容方法签名及原有 JSON context 固定策略；本轮前已存在的流式顺序改动不进入此提交。
- `services/agent_context.py`、`services/sandbox_sessions.py`：Run 环境和隔离 allowlist。
- `apps/api/pi/extension.js`、`extension.test.js`：注册条件、业务/通用/旧结果恢复并存。
- `tests/test_compute_pi_rpc.py`：旧调用场景明确启用兼容开关，保留原端到端断言。
- 新 `tests/test_compute_tool_compatibility.py`、`tests/postgres/test_compute_tool_compatibility_pg.py`：默认、显式启用、错误类型、用户不能覆盖、重启/幂等与账本一次预占。

## RED / GREEN

| 验证 | 结果与环境 |
| --- | --- |
| 新 Run 策略 RED | 12 failed；缺准入字段、服务器设置和隔离 env |
| Pi 表面 RED | 原9项通过，新2项失败；通用工具在新模式仍出现 |
| 后端核心 focused GREEN | 24 passed，无skip；临时 SQLite/mock身份，包含模型 context、Sandbox 和 compute 契约 |
| 真实已安装 Pi RPC +计算回归 | 52 passed，无skip；其中PG16零跳过；loopback模型/工具替身，涵盖通用与业务真实RPC、terminal usage、receiver、release预算和幂等 |
| PG重启/策略/账本 | 2 passed，PG2零跳过；独立PG17随机schema，历史策略和原reservation数量不变 |
| Pi扩展 | 11个内部场景通过；Node22入口也保持原 `node --test extension.test.js` |

日志与JUnit在 `/tmp/pskit-remediation-20261012/pr5/`。初始一次测试启动缺 `PYTHONPATH`、一次选错不存在的测试文件，以及公共请求测试误用了 mock store/默认 session 已记录为环境或fixture错误，修正后才计入上述完成结果。没有增加skip、放宽断言或扩大执行时限。完整最终新栈与对应远端 HEAD 的六项CI结果在最终索引记录。

## 回滚与边界

revert 本阶段，Pi 恢复原目录非空时注册通用工具的行为；内部端点与 transcript 格式未变。已写入的 JSON 策略字段可以保留，旧程序忽略它，不需要删历史、迁移/降级schema或退还资源预占。实际生产仍未部署本轮修改。

真实历史通用调用使用量、受控采用结果和最终删除决策尚未取得，不据本地用例移除旧工具。模型资格继续受原 policy 限制；本阶段不授予模型权限、GPU额度或 OpenSandbox 特权。

## 最终整合与镜像

与PR-0/1、PR-2/MCP、最终模型兼容及控制器一起，在干净source运行全量：Backend **1516 passed / 1原有外部Supabase skip**（PG **234 passed/0skip**），Deployment **458/0skip**，Frontend **400**、Pi **11**、Compose **9**、契约一致、镜像 **3**和 **316**运行源哈希一致。修改前的流式顺序/准入SQL等未进入候选。

精确parent `463b2ef` 的 [六项远端CI全部成功](https://github.com/Alakazamc/pskit-2.0/actions/runs/38163205284) 后才提交/推送本阶段。最终HEAD门禁由交付答复提供精确run链接，完整[交付索引](../2026-10-12/REPORT.md)包含逐项结果、已知环境差异和回滚。镜像 `sha256:0bf9cb9388bc1f66d44e7943d9ab725cbbf857a6dd1953ca18f9f859c289b471` 仅本地保留，未部署。
