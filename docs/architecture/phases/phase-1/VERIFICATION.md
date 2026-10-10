# Phase 1 验证

本地 Python 3.12.9、Node 24.13.0；使用既有锁定依赖和 jsdom/fake fetch/timers。
不使用真实模型、GPU、SMTP、用户输入或生产数据库。

| 范围 | 命令 / 证据 | 结果 |
|---|---|---|
| 流式定向 | `cd new_frontend && npm test -- src/api/http.test.ts src/api/sse.test.ts src/features/chat/events.test.ts src/features/chat/useRunEvents.test.tsx src/features/chat/reconnectPolicy.test.ts` | 最终 110 passed；独立复核 11 passed / 59 deselected（仅选定新边界） |
| 前端静态 | `npm run typecheck`、`npm run lint` | passed |
| 发布/认证 | `new_backend/.venv/bin/python -m pytest -q deploy/agent/tests/test_safe_release_entrypoint.py deploy/agent/tests/test_production_config.py deploy/agent/tests/test_single_postgres_stack.py deploy/agent/tests/test_auth_security_regression.py deploy/agent/tests/test_host_nginx_auth_limits.py -k 'not render_has_no_public_db_port' -p no:cacheprovider` | 89 passed、1 deselected，4.25s |
| Bash | `bash -n deploy_to_cloud.sh deploy/agent/stack.sh` | passed |
| 预检 lint | `new_backend/.venv/bin/ruff check deploy/agent/scripts/production_config.py deploy/agent/tests/test_production_config.py deploy/agent/tests/test_safe_release_entrypoint.py deploy/agent/tests/test_auth_security_regression.py` | passed |
| diff | `git diff --check` | passed |

先失败证据包括跨批次 old/reset/new、同批重复、CRLF 字节拆分、公开测试 key、
旧根脚本执行外部命令、生产 observe 放行。独立审阅又发现并要求修复同 chunk
有效前缀丢失、健康 idle 连接错误累计、dotenv 有效值不一致以及慢 fetch 误判
健康；没有通过删除安全断言来消除这些发现。

## 集成基线与边界

- 初始前端全量为 326 passed / 11 failed，另一个旧 Skill fixture 缺少 files
  导致 unhandled error；Phase 2 使用完整新合同 fixture 和现有 UX 断言修正。
- 初始后端全量为 785 passed / 71 failed / 1 skipped / 3 errors。大部分 live
  fixture 缺少现行必需 CSRF 配置；其他涉及旧目录/schema/provider 断言。
  这不计为 Phase 1 全量通过，后续结果单列 Phase 2。
- 一个组合测试卡住源于本地 sandbox 拒绝 asyncio 自唤醒 socket；同一源码在
  受准许的本地测试进程中通过。没有为环境问题放松业务安全边界。
- 本阶段部署检查使用命令 stub；被 deselect 的实际 Compose render 在 Phase 2
  使用合成配置单独执行。生产运行时和浏览器真实代理断线均 not tested。

最终独立审阅为 Spec PASS / Quality PASS，0 unresolved；复核了慢 fetch/header
失败不能误判健康、consumer 错误不能覆盖、终态/取消以及同 chunk 有效前缀。
已完成部署安全提交 `40de1c9`；前端为单独提交。
