# Phase 2 验证

本地 Python3.12.9、Node24.13.0，固定 Pi0.87.1、PostgreSQL17.6。
PG 使用本任务新建的 loopback/tmpfs 容器；每组测试创建独立 schema/database。
没有使用真实用户、生产凭据或供应商请求。

| 检查 | 实际结果 | 本地证据 |
|---|---|---|
| CI 同等后端 | 862 passed / 1 外部 Supabase integration skip；170 PG passed / 0 PG skip，164.79s | `/tmp/pskit-phase2-ci-backend-rerun/backend.log`、`backend.xml` |
| CI 同等前端 | 48 files / 357 passed；typecheck、lint、build/Molstar passed | `/tmp/pskit-phase2-ci-frontend-final/frontend.log`；依赖缓存隔离修正后重复全套同样通过 `/tmp/pskit-phase2-frontend-final.log` |
| CI 同等部署 | 248 passed / 0 skipped，14.94s | `/tmp/pskit-phase2-ci-deploy-final/deployment.log` |
| Pi 本地协议 | Node extension 测试文件通过；依赖缓存隔离修正后后端 Pi 协议 10 passed | `/tmp/pskit-phase2-ci-pi-protocol-final/` |
| 合成 Compose | 9 个配置组合通过，仅 render，无启动云端 | `scripts/ci_render_compose.py` |
| 正式 Dockerfile/context | 实际构建当前本地源码，非 root/Python3.12/Node22/Pi/import/secret exclusion 3 passed，4.75s | `deploy/agent/tests/test_backend_image.py`；镜像见下 |
| 真实 PG auth namespace | 7 passed，含两个业务角色实际 SELECT 被拒绝 | `/tmp/pskit-phase2-auth-pg.log` |
| Manifest | 12 passed，含旧歧义哈希反例、安全字段与 overlay 门禁 | `deploy/agent/tests/test_release_manifest.py` |
| 契约 | Python/OpenAPI/Event/Compute/TS 在临时目录再生逐字节一致 | `scripts/check_new_stack_contracts.py` |
| 固定 LiteLLM callback | **5 passed**，1.965s；同步/异步、流/非流对照共 12 个 wire 子场景，实际 SDK/callback，容器禁网 | `/tmp/pskit-phase2-litellm-final.log`；固定镜像离线入口见 `infra/litellm/COMPATIBILITY.md` |

镜像为本地未发布验证快照 `pskit-agent-backend:remediation-local-20261010`，Docker 实际 ID/RepoDigest：
`sha256:8dd93e18fc18a69e349bfd36474dcea12f41fb19685d55a0a2928a9141b214a1`。
使用正式 `deploy/agent/backend.Dockerfile` 和 `new_backend` context；基础 Node/Python 镜像保持原固定 digest，下载采用镜像站。不冒充已提交源码的发布 Manifest。

## 失败与修正记录

- 首次扩大后端基线为 785 passed / 71 failed / 1 skip / 3 errors：逐项修正旧 fixture/断言，另发现并修复专属表导出缺陷。没有删除权限或降级拒绝检查。
- 首次 canonical runner 为 856 passed / 3 failed / 3 errors / 1 skip。六项错误均为本地 Snap Docker 无法读取 `/tmp` 合成配置。使用本地 config-only 非 Snap Compose wrapper 重跑后得到上表结果，代码没有宽泛 skip 或调整业务逻辑。
- 本地 sandbox 拒绝 asyncio 自唤醒 socket，使线程池测试挂起；获准在独立本地测试进程执行同一测试通过，未修改应用规避机制。
- 审查发现 Manifest 哈希缺少分帧：构造两个不同文件树得到旧相同输入，修为路径长度 + UTF-8 路径 + 文件 SHA256，反例通过。
- 固定 LiteLLM 容器初次检查：5 tests / 4 OpenAI 子场景 error，原因为客户端 fixture 类型错误；保留真实 callback 和所有断言，使用 OpenAI SDK + MockTransport 后全部通过。镜像下载后实际 digest 为 `sha256:a68b34df312f1e72914f0886384517fa4fdb09013480460b80a4e3a40104376b`。

远端 GitHub CI、真实 Supabase integration、新模型供应商、云端 preflight/smoke、Staging 和生产均 **not tested**。
依赖目录隔离的最后修正后，前端全套与 Pi 协议已重跑；后端其他测试源码未变，未把这次定向重跑写成第二次全量运行。
