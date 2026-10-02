# New Backend 模块整理设计

## 目标与边界

`new_backend/` 已承载独立 FastAPI、Pi 持久运行、配额、MCP/AF3 适配和游客生命周期。下一步让目录表达这些职责，并为函数提供可核对的 Google 风格 docstring。重排期间保留公开 HTTP/OpenAPI、`PersistentConversationStore` 的导入路径、SQLite schema、mock 模式及业务行为。用户已要求 TDD；现有 HTTP、持久化和真实 Pi 替身测试是重排的行为基线。

## 当前问题

- `app/domain/persistent_conversation.py` 有 2,134 行、98 个方法，混合工作区、聊天、Pi Run、AF3、用量和恢复调度。修改一个领域时需要读整份文件。
- `app/db/migrations.py` 有 442 行、40 个函数，核心 schema 迁移与身份、目录、OAuth、工具等组件迁移共处一文件。
- AST 盘点显示 `app/` 约 500 个函数缺少 docstring；直接批量添加从函数名拼接的说明会产生误导性文档。

## 方案比较

1. **仅加章节注释。** 改动小，但仍需在两个大文件内跨区域查找，也无法给模块提供清晰接口。
2. **按领域拆为内部实现模块，维持现有公共入口（采用）。** 把持久化方法分到 workspace、run、AF3、usage、scheduler 等文件；入口继续导出同一个 `PersistentConversationStore`，调用方和测试不用迁移。把迁移按 core/components 拆分，继续从 `app.db.migrations` 导出原函数。每次只移动一组职责并跑目标测试。
3. **重建多个独立 Repository 并改所有调用方。** 长期可进一步降低耦合，但这轮会同时改变服务层、事务边界和数据库连接，风险明显更高。

## 模块接口

`PersistentConversationStore(path, ...)` 仍是调用方唯一需要认识的持久会话接口。内部使用按职责分组的方法集；共享 SQLite 连接及 `BEGIN IMMEDIATE` 事务仍由入口持有。内部文件只供该实现使用，暂不对 API 层暴露新类。`app.db.migrations` 继续导出 `migrate_core_database`、`migrate_identity_policy_schema` 等现有入口；拆分后不引入第二套 schema 版本。

建议的内部目录：

```text
app/domain/persistent_conversation/
  __init__.py             # 公开 PersistentConversationStore
  store.py                # 连接、schema 初始化和组合
  workspace.py            # 项目、会话、Skill 设置
  runs.py                 # 消息、事件、计划、Pi 会话
  af3.py                  # 审批、任务、租约、产物
  usage.py                # Token/GPU 用量、限制与模型调用记账
  recovery.py             # mock 推进、唤醒和中断恢复

app/db/migrations/
  __init__.py             # 维持旧导出
  core.py                # 1–14 核心升级
  components.py          # 身份、目录、OAuth、工具等组件升级
  common.py              # 事务和补列工具
```

## Google 风格文档规则

公开方法写明调用效果、返回值及关键失败条件；事务和权限相关方法说明原子性和所属范围。非显然参数使用 `Args:`，有返回值使用 `Returns:`，由本模块主动抛出的业务异常使用 `Raises:`。简单私有 helper 可用准确的一行 docstring。先用 AST 清单作为完成度检查，再逐模块人工核对说明与实际代码一致；不把测试函数计入产品代码范围。

## 验证与回滚

每个搬迁步骤在同一工作区完成，先跑对应测试，再跑后端完整测试、Pi 扩展测试、前端 typecheck/lint/build 与 OpenAPI 生成检查。只移动实现、不改业务流程；如果目标测试失败，回滚该步骤的移动并定位 import、MRO 或事务差异。真实外部 Supabase/模型/MCP/AF3 仍按替身契约边界记录，不因目录变化宣布联调完成。
