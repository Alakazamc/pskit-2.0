# Phase 2 — 新栈 CI、契约和制品门禁

代码审查和本地同等门禁通过；固定 LiteLLM 容器的离线协议已实际执行。远端 CI 与云端资格不在本次验证范围。
未 push、SSH、部署、调用付费模型或操作真实数据库。

- 新增新栈六项 CI：Python3.12/PG17、React、固定 Pi、契约、固定 LiteLLM callback、合成部署与正式镜像。旧栈 CI 保留，无自动发布。
- 本地同等入口复制源码到临时目录，剔除私有配置和数据；临时 HOME/cache，限制进程组与耗时。PG 必须为独立 loopback 17，实际 PG suite 不能为空或跳过。
- 修正旧测试的显式 CSRF 配置、当前 provider doubles、schema/文案断言。保留权限、所有权、配额、lease、drain 和私密工具载荷检查。没有弱化线上安全默认。
- 发现真实导出缺陷：`agent_data_migrate.py` 漏列 PostgreSQL 专属的 Tool Product 和 workspace attempt 表。补齐精确集合，保留共享表一致性检查；任何专属表有状态时仍拒绝降级导出，不丢弃状态。
- 单独同步已有 API 的生成物：Skill 路由/类型和可选 Catalog metadata；`tool.updated` 引用已有带可选 summary 的类型。独立审查未发现既有 OpenAPI 路径/字段移除或值改变。
- 新增固定 LiteLLM1.100.3 的同步/异步、流/非流、PinCC/官方 Anthropic/OpenAI callback 对照及退役手册；此验证不会调用在线供应商。首次容器检查发现 OpenAI fixture 错把 httpx client 当 SDK client，改为真实 OpenAI SDK 包装 MockTransport 后通过。
- 合成 Compose 覆盖九种环境组合；Staging 只允许已有明确且只读的 compat 挂载，任意源码或可写挂载仍拒绝。
- Manifest v1 记录不可变镜像、源码、dist、schema、Pi/LiteLLM/compat、完整有序 overlays 和验证结果。`not_tested` 可如实记录但不能通过发布资格。dist 哈希采用长度分帧，防止路径与内容边界歧义。

审查修正了 dist 哈希的构造碰撞和 auth ACL fixture 的真实性问题。最终 ACL 用独立数据库中准确的 `auth.users` namespace，同时验证系统管理员可读、两个业务角色实际查询被拒绝。
