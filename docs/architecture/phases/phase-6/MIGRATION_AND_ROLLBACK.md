# Phase 6 迁移与回退

## 本地路径变更

| 原路径 | 新路径 | 范围 |
| --- | --- | --- |
| `new_backend/` | `apps/api/` | FastAPI、Pi、skills、API tests、依赖和 Python package |
| `new_frontend/` | `apps/web/` | React/Vite、browser checks、npm 锁文件及前端约定 |

同步更新的新栈引用包括 `.github/workflows/new-stack.yml`、`deploy/agent/compose*.yaml`、backend/web Dockerfile、CI 与 Compose 合成检查、契约导出、包相对路径和开发/部署文档。旧栈恢复文件未搬迁。

## 本地代码回退

目录迁移作为一个独立提交。需要回退时：

1. 停止只在本机 `apps/api` / `apps/web` 启动的服务；确认没有本地任务在写数据。
2. 先检查并备份被忽略的 `apps/api/.env`、`.venv`、`data/`（包括 SQLite 与 Pi 会话）、`apps/web/.env.local`、`node_modules/` 和 `dist/`。它们不会出现在 Git 提交中，也不会随 `git revert` 自动移动。
3. 若需要在回退后继续使用这些本地数据，先确认旧目标 `new_backend/`、`new_frontend/` 不存在或为空，再把对应忽略数据搬回原位置；不得覆盖已有目录。SQLite/Pi 会话文件需校验数量和大小。
4. 对纯迁移提交执行 `git revert <migration-commit>`。该操作只恢复跟踪文件与路径引用；不会碰 `.env`、本地数据库、会话、依赖缓存或 dist。
5. 按旧 checkout 对应版本启动服务，确认本地 API、前端及需要的 Compose 配置路径。

不要通过删除目录、卷或历史记录完成回退。若 Git 回退因残留 `apps/*` 忽略目录冲突而停止，保留目录内容并先按上述步骤搬走或备份，再继续。

## 云端边界

本次没有改阿里云 checkout、宿主 bind、Nginx root、运行容器或生产数据，因此云端无需回退，既有已验收制品继续运行。未来切换云端源码路径前，需单独完成 Staging 同制品 source-bind 与 rollback 演练，保留当前远端 checkout/旧制品；不能把本地 Git revert 当成线上回滚。
