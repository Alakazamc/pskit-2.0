# 首轮对话自动命名

## 行为

新前端创建普通、项目和工具入口对话时发送 `auto_title: true`。先以首条消息的摘要显示临时标题，等首个完整助手回复提交后，再由独立后台请求概括首轮用户文本和助手回复。流式回复完成时仍立即恢复发送按钮，命名不会进入 Pi 会话记录。

提示词要求使用用户语言，建议中文不超过 16 字、英文不超过 8 个词，返回单行标题；服务端最多保留 48 个字符。标题更新后，侧栏和当前对话页头同步刷新，草稿、模型选择、推理强度和地址保持原样。刷新仅在标题待生成期间运行，完成或网络错误后停止。

每个对话只排队一次。手动改名优先；历史对话和没有显式启用自动命名的 API 客户端保留原来的标题。取消、失败和等待后台计算的未完成回复不触发命名。标题生成失败、模型未开放或 Token 额度不足时保留临时标题，不影响已经完成的回复。

## 配置与计量

```dotenv
# 留空沿用首轮对话选择的模型；可填写网关中已开放的小模型别名。
RESEARCH_AGENT_TITLE_MODEL=
# 单次命名总超时，允许 1–60 秒。
RESEARCH_AGENT_TITLE_TIMEOUT_SECONDS=15
```

模型由现有 ModelPolicy 校验，调用现有服务端模型网关。LiteLLM 接收可信用户标识，浏览器不接触模型密钥。额外调用先预留 Token，按网关报告的用量结算，计入月额度和每日活动统计。确定的 HTTP 4xx 拒绝退回预留；超时、断线或无用量报告时保留保守额度，不自动重试未知成本的请求。

命名工作记录保存在应用数据库。生产使用 PostgreSQL 迁移 **7**，离线测试使用 SQLite 迁移 **15**；待执行请求可在重启后领取。已经领取但崩溃的请求过期后标记失败，不重复发送。人工改名与生成标题使用同一事务锁，避免多个后端进程相互覆盖。游客数据清理包含命名记录。

## 验证边界

测试通过公开 HTTP 和 React UI 行为验证，Pi、身份提供商和模型网关使用外部替身。PostgreSQL 验证在本机独立测试服务内创建临时 schema，结束后删除。没有真实付费模型调用，也没有更改云端服务或生产数据。

- 后端命名用例：首轮完成时机、独立请求、选用小模型、额外计量、仅调用一次、手动改名冲突、跨用户隔离、失败/超时/额度不足回退、取消回复和重启。
- 前端 HTTP 用例：普通与项目对话显式启用命名、完成后更新标题、保留草稿与 URL、成功及网络失败后停止刷新。
- PostgreSQL：版本 7 迁移、持久化、重启去重及每日 Token 活动。
- Chromium：明暗主题 × 桌面/手机，覆盖中英语言、个人/项目对话，确认模型与推理强度显示保持不变，无额外浏览器模型请求、页面错误或横向溢出。

浏览器截图及观察结果：`/tmp/pskit-session-titles/`。TDD 首轮失败证据：`/tmp/pskit-title-red.log`、`/tmp/pskit-title-ui-red.log`；网络刷新问题失败证据：`/tmp/pskit-title-ui-error-red.log`。

后端全量回归 `PYTHONPATH=..:. .venv/bin/pytest tests -q`：**440 passed, 195 skipped**。跳过项依赖专门的 PostgreSQL、Docker/沙箱或其他集成环境；其中本次 PostgreSQL 命名与 schema 测试单独连接本机测试数据库运行，**4 passed**。迁移及命名针对性回归 **31 passed**。Ruff 与 `git diff --check` 通过。日志分别为 `/tmp/pskit-title-backend-all-final.log`、`/tmp/pskit-title-pg-final.log`、`/tmp/pskit-title-schema-final.log`。

前端 `npm test -- --maxWorkers=2`：**35 文件、220 测试通过**。`npm run typecheck`、`npm run lint`、`npm run build` 通过；构建仍有既有的大 chunk 提示。Chromium 四种组合全部通过。全量测试在并发编译时曾超过 Testing Library 默认的一秒等待，完成回复的断言改用五秒上限；生产交互没有增加延时。日志为 `/tmp/pskit-title-frontend-all-final.log`、`/tmp/pskit-title-typecheck-final.log`、`/tmp/pskit-title-lint-final.log`、`/tmp/pskit-title-build.log`、`/tmp/pskit-title-browser-final.log`。
