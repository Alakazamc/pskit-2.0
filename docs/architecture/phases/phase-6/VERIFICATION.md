# Phase 6 验证记录

日期：2026-10-10（Asia/Shanghai）

## 结果

| 检查 | 结果 |
| --- | --- |
| Backend 完整新栈门禁（临时 PostgreSQL 17） | 1204 passed，1 个外部 Supabase Auth/Storage 集成测试按约定跳过；PostgreSQL gate 232 passed、0 skipped。`ruff` 和 `compileall` 通过。 |
| Frontend 完整门禁 | typecheck、lint、53 个测试文件/387 项测试、build 与 Molstar verifier 通过；保留 Vite 既有 large-chunk 提示。 |
| Pi 门禁 | Node Pi 扩展测试 1 passed。 |
| Deployment 完整门禁 | 在仓库内隔离临时目录运行：446 passed、1 skipped；2 个要求 host 临时目录位于 `/tmp` 的 staging Nginx 测试因 Docker Snap 对仓库临时目录的安全限制未适配该轮路径。随后将整个 `test_staging_nginx.py` 放回其要求的 `/tmp` 条件单独重跑：4 passed。 |
| 迁移路径回归 | Compose、沙箱和 smoke 拒绝策略 8 passed； admission CLI/cloud quota 两个 PostgreSQL 测试文件 7 passed。 |
| 归档基准路径 | 新增 `--source-ref` Git archive 回归先在旧校验下按预期失败；修正 `apps/api/app` 三段路径前缀及快照落点后，`scripts/test_benchmark_new_stack.py` 9 passed（`--asyncio-mode=auto`）。 |
| Compose 合成渲染 | `scripts/ci_render_compose.py` 检查本地、Staging、生产及 OpenSandbox 等 9 个布局通过；没有启动这些应用服务。 |
| Backend 镜像 | 使用 `apps/api` 作为 context，按固定基础镜像 digest 和现有镜像源参数构建成功；隔离容器确认非 root UID 10001、Python 3.12、Node 22、Pi 0.87.1、模块导入及 `/data`/`/var/lib/pskit-mcp` 写权限。临时镜像与基础镜像别名已清理。 |
| Pi/契约/本地链接 | `check_new_stack_contracts.py` 通过；Markdown 活跃文档本地链接检查缺失 0 项；`git diff --check` 最终复核见交付记录。 |

## 失败归因与测试环境

首次完整运行发现目录搬迁后 5 个测试路径计算仍按旧目录层级，导致 Compose、sandbox smoke、admission CLI 和 GPU quota 模板路径指向 `source/apps/...`；修正为新仓库根层级后，相关 8 项与 7 项 PostgreSQL 回归均通过。首次部署测试还把 Docker Compose 测试文件放在系统 `/tmp`，Docker Snap 无法读取；后续将 CI snapshot 与临时文件放在仓库隔离目录后，除两项自身明确拒绝非 `/tmp` 的 staging host-root 测试外均通过，并按其安全前提单独重跑通过。

临时 PostgreSQL 仅监听 `127.0.0.1`，无持久卷；测试后按唯一容器名删除。没有连接云端、生产数据库、真实模型或 GPU。

## 未验证

- 阿里云 source-bind、生产/Staging Nginx 静态根目录与相同制品的主机验收。
- 真实 Staging cutover/rollback 与线上旧 checkout 的恢复。
- GitHub 远端 CI（未 push）。
- 外部 Supabase Auth/Storage 集成（所需独立测试凭据未配置）。
