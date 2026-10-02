# New Backend 模块整理实施计划

> **For agentic workers:** 按步骤在当前共享 checkout 执行；现有 `new_backend/` 尚未纳入 Git，不能为任务建立会丢失这些文件的独立 worktree。

**Goal:** 把持久会话与迁移实现按职责组织，并为 `new_backend/app/` 函数补齐准确的 Google 风格 docstring。

**Architecture:** 保留 `PersistentConversationStore` 与 `app.db.migrations` 的公共导入接口；内部按工作区、Run、AF3、用量、恢复和迁移类型分文件。每次只搬迁一组，现有 HTTP/SQLite/Pi 测试校验行为不变。

**Tech Stack:** Python 3.12、FastAPI、SQLite、Pydantic、pytest、Pi RPC。

**Spec:** `docs/superpowers/specs/2026-10-02-backend-module-cleanup-design.md`

## 全局约束

- 不改变 `/api/v1` 和 `/internal` 契约、SQLite 版本或 mock/live 配置语义。
- 不把 docstring 写成从函数名推导的空泛语句；权限、事务、重试等行为须与代码一致。
- 测试边界沿用用户已同意的公共 HTTP、持久会话接口及 Pi 替身；结构要求以导入兼容和 AST 清单验证。
- 目录搬迁过程中的完整后端测试使用已批准的 pytest 沙箱权限。

## Task 1：固定结构和文档基线

**Files:** 新增 `new_backend/scripts/check_docstrings.py`、`new_backend/tests/test_backend_imports.py`。

- [x] 写导入兼容测试：现有 `app.domain.persistent_conversation.PersistentConversationStore`、`app.db.migrations.migrate_core_database` 可用；记录 OpenAPI 快照哈希 `28d2cd1ae80131aae3da78ed3c772cd41eb92fc772032eff735029c49b4dcab8`。
- [x] 写 AST 文档清单工具，输出每个模块缺少 docstring 的函数数；支持 `--check` 在全部完成后失败关闭。
- [x] 运行目标测试、清单和完整后端测试，记录当前基线：拆分前 490 个函数缺少文档字符串，完整后端测试通过。

## Task 2：拆分持久会话实现

**Files:** 新增 `new_backend/app/domain/persistent_conversation/{__init__,store,workspace,runs,af3,usage,recovery}.py`；删除旧同名 `.py`；修改引用时保持导入路径。

- [x] 先写新目录的公开导入结构测试，确认新结构尚未实现时失败；现有双实例测试仍是行为基线。
- [x] 按工作区、消息/Run、AF3、用量、恢复五组搬迁；入口只组合内部实现，共享连接和原事务 helper。
- [x] 运行定向 pytest 与后端完整测试，含 Pi 扩展测试；最近一次完整结果为 351 passed。
- [x] 核对模块之间调用，消除循环导入和重复 schema 定义。

## Task 3：拆分迁移实现

**Files:** 新增 `new_backend/app/db/migrations/{__init__,core,components,common}.py`；删除旧 `migrations.py`。

- [x] 用现有 `test_component_migrations.py` 与核心 schema 迁移测试锁定升级、回滚和版本号。
- [x] 移动核心和组件迁移，公共入口保持原名，避免修改调用方。
- [x] 跑迁移目标测试及后端完整测试；验证老库升级路径。

## Task 4：逐模块补全函数文档

**Files:** `new_backend/app/**/*.py`、必要的模块 README。

- [x] 先为持久会话、迁移、身份与游客清理模块补全 docstring，人工检查事务、异常与返回语义。
- [x] 再覆盖 API、适配器、服务、其余 domain 和 ports；每组运行 AST 清单及相关测试。
- [x] 对全部 `app/` 运行 `python scripts/check_docstrings.py --check`；结果为 0 个缺失函数。

## Task 5：交付校验

- [x] 重导出 OpenAPI 和前端类型；新增路由文档被 FastAPI 导出为 description，数据模型和路径行为保持原样，导出一致性测试通过。
- [x] 后端 `python -m pytest -q`（351 passed）、`python -m compileall -q app scripts`；前端 `npm test`（129 passed）、`typecheck`、`lint`、`build` 均通过。
- [x] 更新 `new_backend/README.md` 的目录说明，并记录真实 Supabase、模型网关、MCP、AF3 服务尚未部署的联调边界。
