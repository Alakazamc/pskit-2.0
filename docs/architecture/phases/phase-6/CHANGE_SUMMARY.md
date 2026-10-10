# Phase 6：新版前后端纯目录迁移

## 结果

本地新版源码已从 `new_backend/`、`new_frontend/` 迁到 `apps/api/`、`apps/web/`。迁移只改变文件位置和引用路径，没有改变业务实现、数据库 schema、公开 API 或运行职责。迁移是独立变更，未与 Phase 1–5 的业务改动混合。

已同步新栈 CI、Compose build context/Dockerfile、Web Docker COPY、契约导出、测试夹具、Pi/Python 工作目录、文档入口及新后端的数据忽略规则。目录层级变化涉及仓库根路径的测试/辅助脚本也已修正。旧版 `backend/`、`frontend/`、旧 workflow 和恢复入口保持原位；历史发布记录保留当时的路径，不做全局替换。

迁移后在 `apps/api/`、`apps/web/` 下保留本地被忽略的 `.env`、`.venv`、SQLite/Pi 会话数据、`node_modules` 与 `dist`；这些运行数据不进入 Git。旧 `new_*` 目录不再保留影子代码副本。

## 本地边界与未完成 Gate

- 本地源代码、CI、Compose、契约生成及镜像 context 已切到 `apps/*`。
- 阿里云已有 checkout 的 source-bind、宿主 Nginx 静态根目录、真实 Staging 同制品演练和线上回退未核对/未切换；云端仍按既有已验收制品和 checkout 路径运行。
- 旧版 `backend/`、`frontend/` 的独立归档尚未实施；继续保留旧 workflow、运维与恢复工具，直到逐项证明无活动引用。
- 本轮没有 SSH、push、部署、修改真实数据库/密钥或删除历史数据。

## 检查与回退

完整命令和通过范围见 [验证报告](VERIFICATION.md)。跟踪文件可用本阶段单一迁移提交整体 revert。被忽略的本地环境/数据不会由 Git 自动搬回；若要回到旧目录布局，先停止本地服务，确认目标目录为空，再手动搬回并核对 `.env`、`.venv`、SQLite/Pi 会话、`node_modules` 和 `dist`，不得覆盖或删除这些数据。详见[回退说明](MIGRATION_AND_ROLLBACK.md)。
