# 匿名科研体验设计

## 意图与范围

用户无需邮箱即可先与 Agent 对话、创建项目和会话、上传少量文件；之后可把**当前游客账号**升级为一个新邮箱或 Google 账号，保留原有研究记录。Python 仍是浏览器唯一 API 入口，Supabase Auth 负责生产身份，Pi、模型网关、任务和用量由 Python 管理。旧 `backend/`、`frontend/` 不参与。

首版不把游客数据自动合并到**已有账号**。游客在此场景登录前要看到明确提示，并可取消登录、先保存当前研究。GPU/AF3 只对已注册用户开放；MCP 对游客默认关闭，只有人工标记为低成本且无外部副作用的能力才能放行。

## 方案选择

采用 Supabase Auth 原生匿名用户。Supabase 为游客创建用户 ID、访问令牌和刷新令牌，升级身份时保留同一个用户 ID。Python `SupabaseIdentityAdapter` 调用 Supabase Auth；React 只访问 Python。此方案沿用现有按 `user_id` 归属的项目、会话、文件、Run 与用量，不创建第二套 `anonymous_id` 所有权模型。

没有选择自签游客 Cookie：它会让生产环境出现两种身份验证和迁移路径。没有选择浏览器指纹作为身份：指纹可能碰撞或变化，不能证明文件所有权，也会产生隐私跟踪风险。清除 Cookie 后无法安全找回游客记录；防刷通过小额度、服务端限速、CAPTCHA 和成本门槛处理，不尝试通过指纹自动合并身份。

依据：[Supabase 匿名登录](https://supabase.com/docs/guides/auth/auth-anonymous)、[身份关联](https://supabase.com/docs/guides/auth/auth-identity-linking)、[W3C 浏览器指纹建议](https://www.w3.org/TR/fingerprinting-guidance/)。

## 身份与升级

1. 登录页展示双语“先体验”。只有用户点击后才创建游客，避免打开首页或爬虫访问自动产生 Supabase 用户。`POST /api/v1/auth/anonymous` 接受可选 `captcha_token`；live 环境开启 CAPTCHA 时必填。若请求已带有效游客刷新 Cookie，直接恢复同一游客；若已有会员会话，返回冲突而不覆盖。其余请求由 Python 先执行创建速率限制，再将令牌交给 Supabase `/auth/v1/signup`，验证返回用户确实匿名，设置现有 HttpOnly 刷新 Cookie，返回 `AuthSessionResponse`。
2. `UserIdentity` 增加 `is_anonymous: bool`。Python 从 Supabase `/auth/v1/user` 的服务端验证结果取得标记；缺失或类型不正确时按游客权限处理，绝不从前端传入的字段确定权限。mock 身份适配器返回相同契约。`GET /me` 和 `POST /auth/refresh` 都返回标记。
3. 账号等级保存在 Python 的持久身份策略表，以便 HTTP 请求结束后 Pi、模型代理和任务 Worker 仍能判定权限。每次成功验证 Supabase 用户时同步等级；游客可转为会员，不能因旧游客令牌或客户端字段被降级或升级。只有 Supabase 确认身份已绑定且 `is_anonymous=false` 才切换等级。
4. 游客绑定新邮箱：`POST /auth/upgrade/email` 在当前游客凭据下调用 Supabase 更新邮箱；`POST /auth/upgrade/email/verify` 使用 `email_change` 验证码验证新邮箱；之后 `POST /auth/password/update` 设置密码。部署时将 Supabase 邮件模板配置为可输入的验证码。验证前仍按游客计额。前端“普通注册”不得用于保存当前游客数据。
5. 游客绑定 Google：`POST /auth/upgrade/google/start` 使用游客 Bearer 令牌发起 Supabase 手动身份关联，并把一次性 OAuth state/PKCE 与游客 ID 绑定；回调完成后核对 Supabase 返回的同一用户 ID，才刷新会话和等级。现有普通 Google 登录入口继续用于非游客。若该 Google 身份已属于其他账号，返回明确冲突，保留游客会话及文件。
6. 升级保持 Supabase 用户 ID，因此项目、会话、Pi session、文件、任务和用量记录无需搬迁。当前月 Token 已用量继续计入升级后额度；不叠加或赠送一份游客余额。已有账号合并另作设计，不能靠 IP、指纹或文件哈希自动认领。

游客退出会失去找回同一游客身份的方式。前端在退出前说明这一点，不在退出后自动创建新游客。刷新 Cookie 沿用当前 HttpOnly、`SameSite=Lax` 和生产 `Secure` 设置；访问令牌沿用 live 前端的内存保存方式，不写入 localStorage。

## 配额与能力边界

默认游客策略：每月 20,000 原始 Token、总上传 10 MiB、每日 GPU 0 分钟、最多 1 个并发 Agent Run。数值放在 Python 配置中，可按部署调整；已有会员默认策略保持当前值，管理员对特定用户设置的覆盖值优先。Token 仍由 Python 预留与结算，前端显示“Token 额度”，不显示 New API 内部点数。

等级表只描述用户类型；配额计算在同一个共享策略入口解析“管理员覆盖值 → 等级默认值”。`/usage` 的 `limit`、`remaining` 必须与消息准入、模型代理预扣和 GPU 任务预留使用相同策略。异步 Pi Run 保存的是 `user_id`，所以内部模型请求和恢复执行也必须从持久策略读取等级，不能依赖当初 HTTP 请求里的 `UserIdentity`。

游客文件限制同时覆盖 JSON 文本上传和流式上传：单文件最多 2 MiB，总已保存文件最多 10 MiB；最终“检查总量 + 插入文件”是同一 SQLite 写事务，删除后释放占用。上传前可做快速大小拒绝，但不能用它代替最终事务检查。PDF 解析已有大小和资源限制，游客限制在其外层叠加。`/usage` 增加 `storage` 计数器，单位为 bytes，周期为 lifetime，前端据此展示文件余额。

AF3 在公开提交、Pi 内部提交、审批和任务恢复前均检查持久等级；游客得到稳定 `LOGIN_REQUIRED` 错误，不创建任务也不预留 GPU。MCP 工具目录和直接调用、Pi 工具白名单使用同一游客允许列表；未知或新注册工具默认拒绝，避免 MCP 工具绕过成本策略。已有认证用户继续使用原权限检查。

## 防滥用与生命周期

游客创建端点使用持久化、原子服务端限速：默认同一可信客户端地址每小时最多 10 次。只取 ASGI 中经过可信反向代理处理的客户端地址，不自行相信浏览器提交的 `X-Forwarded-For`。短期限速键是服务端 HMAC 后的地址，原始 IP 和浏览器指纹不入库；键与计数最多保留 24 小时。live 模式必须配置跨实例相同的 `RESEARCH_AGENT_ANON_RATE_SECRET`，并在反向代理层声明可信地址。生产部署启用 Supabase 支持的 CAPTCHA/Turnstile，Python 透传验证令牌；Supabase 自身的匿名注册限速是第二层保护，不作为唯一保护，因为代理调用可能共享源 IP。

不把 Canvas、WebGL、字体、文件哈希或行为轨迹用作自动身份关联。游客创建、消息和上传可另加普通请求速率限制，但不得在共享网络上仅凭 IP 合并或公开他人数据。游客账号和文件需要运营清理：首版以“最后活动超过 30 天且没有未完成 Run/任务”为候选，清理作业删除 Python 数据与 Supabase 匿名账号时记录幂等进度；未配置安全的 Supabase 管理凭据时只报告候选，不执行删除。上线前需要核对真实环境中的数据保留要求。

## 接口与前端行为

| 接口 | 成功/主要失败 |
| --- | --- |
| `POST /auth/anonymous` | `AuthSessionResponse`；429 `ANONYMOUS_RATE_LIMITED`；CAPTCHA 失败 422 |
| `GET /me`、`POST /auth/refresh` | `UserIdentity.is_anonymous` 来自服务端验证 |
| `POST /auth/upgrade/email` | `email_sent`；已有身份冲突时不改变游客会话 |
| `POST /auth/upgrade/email/verify` | 同一用户 ID 的会员会话；无效验证码 401 |
| `POST /auth/upgrade/google/start` | 受当前游客 Bearer 保护的跳转 URL；回调核对 state、PKCE 和用户 ID |
| `GET /usage` | 游客 Token、GPU、storage 额度与剩余量；上限可配置 |
| Agent、上传、MCP、AF3 | 游客超额返回稳定错误码；GPU/AF3 返回 403 `LOGIN_REQUIRED` |

前端先尝试现有刷新会话；失败时展示登录页及“先体验”，不静默创建新游客。游客工作台显示“游客模式”和 Token/文件余额；额度不足时保留草稿并打开登录/升级引导。游客点击邮箱或 Google 入口时明确使用“保存当前研究”流程。双语文案覆盖游客身份、限额、退出不可恢复、身份关联冲突和 CAPTCHA/限速错误。所有调用走 `src/api/`，OpenAPI 生成 TypeScript 类型，React 组件不直接访问 Supabase。

## 验证与交付边界

按已有 TDD 要求先写 HTTP 契约和身份/配额/文件事务的失败测试，再实现后端；随后用 API 替身写前端行为测试，更新 OpenAPI 与生成类型。重点验证并发创建限速、游客与会员隔离、Token 原子扣额、并发上传总量、Pi 内部 AF3/MCP 绕过尝试、游客升级后同一 ID、既有 Google 冲突、断线刷新和跨用户文件访问。mock 环境可跑完整接口契约；真实 Supabase 匿名注册、CAPTCHA、邮箱和 Google 关联需要部署信息后单独联调，不能把替身通过等同于真实联调。
