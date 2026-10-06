# 公网开放注册与认证防滥用设计

日期：2026-10-06。状态：设计已口头确认，等待书面审阅。

## 1. 目标与产品决定

新版 PSKit 面向全网开放邮箱注册，不限制学校域名，也不要求邀请码。这里的“全网开放”只描述会员邮箱注册政策；游客匿名登录仍沿用原设计并在当前生产环境关闭。浏览器继续只调用 Python `/api/v1/auth/*`；Supabase Auth 负责账号、密码、验证码、会话和邮件语义，Python 负责产品接口与共享防滥用状态，宿主机 Nginx 负责入口削峰。

本设计需要同时做到：

- 阻止同一邮箱反复收到注册、找回密码或游客升级验证码。
- 限制单个来源批量消耗 SMTP、Python 和数据库资源。
- 在校园网共享 NAT、移动 IPv6 等正常场景下保持可用。
- 向前端返回稳定的 `429`、`Retry-After` 与双语倒计时。
- 保持 Google OAuth、Token 刷新、聊天 SSE 和普通业务 API 的现有行为。
- 为异常流量保留可观察、可调参和紧急关闭注册的入口。

应用层限流只能保护 Nginx 后面的应用、数据库和 SMTP。占满公网带宽、连接表或大规模分布式来源的攻击仍由阿里云 Anti-DDoS/WAF 等上游设施处理。本期采用不增加 Redis 的分层方案；已有 PostgreSQL 保存跨 Python 实例共享的精确计数。

依据：[Supabase Auth Rate Limits](https://supabase.com/docs/guides/auth/rate-limits)、[Supabase CAPTCHA](https://supabase.com/docs/guides/auth/auth-captcha)、[Nginx request limiting](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html)、[Alibaba Cloud Anti-DDoS Basic](https://www.alibabacloud.com/help/en/anti-ddos/basic-ddos-protection/product-overview/what-is-anti-ddos-basic)。

本规格延续[匿名科研体验设计](2026-10-02-anonymous-experience-design.md)中的 HMAC 标识与可信代理原则，以及[统一 PostgreSQL 持久化设计](2026-10-03-agent-supabase-postgres-stack-design.md)中的生产 PostgreSQL 单一事实来源。Staging/Production 的密钥、数据和邮件服务继续遵守[独立 Staging 环境设计](2026-10-03-agent-staging-isolation-design.md)；公网 Nginx、回环后端、私网管理入口及旧站隔离继续遵守[阿里云后端迁回设计](2026-10-03-agent-aliyun-backend-return-design.md)。本次不能把面向游客的窄化限流器原样扩成高频认证限流，也不能让 live 模式退回 SQLite 或进程内计数。

## 2. 当前系统与已确认缺口

生产身份服务固定为 `supabase/gotrue:v2.196.0`。公开流量经过阿里云宿主机 Nginx 到 Python，Supabase 网关和 Python 后端端口只绑定回环地址。公开 Nginx 仅为 OAuth/验证开放必要的 `/auth/v1/*` 路径，其余认证请求均由 Python 转发。

当前状态：

- GoTrue 未显式设置 `GOTRUE_SMTP_MAX_FREQUENCY`、`GOTRUE_RATE_LIMIT_*` 和 `GOTRUE_RATE_LIMIT_HEADER`；行为依赖镜像默认值。
- GoTrue `v2.196.0` 的默认邮件冷却为一分钟，全局发信预算默认为每 Auth 实例每小时 30 封。该预算不是逐邮箱或逐终端 IP 的完整保护。
- GoTrue 只有收到配置过的可信限流 Header 才能按终端地址执行相关 IP 限流。当前 Python 调用没有传递可信终端 IP。
- Nginx 还没有 `limit_req`/`limit_conn` 认证规则。
- Python 的邮箱注册、找回密码、OTP 验证和游客邮箱升级没有共享的逐 IP/逐邮箱限制。
- 现有 Turnstile 组件只服务游客入口，普通邮箱注册与找回没有 CAPTCHA token。
- Python 已能将 Supabase 的 `429` 和数字 `Retry-After` 转发给浏览器；React 的 `ApiError` 丢弃响应 Header，界面只能显示通用错误，不能倒计时。

## 3. 分层拓扑

```text
Internet
   │
   ▼
Alibaba Anti-DDoS Basic / optional WAF
   │
   ▼
Host Nginx
  ├─ clean and establish client IP
  ├─ coarse per-IP request buckets
  └─ exact auth route rules
   │
   ▼
Python Auth API
  ├─ trusted-client-IP resolver
  ├─ PostgreSQL AuthAbuseGuard
  ├─ Turnstile Siteverify
  └─ public error normalization
   │
   ▼
Supabase GoTrue
  ├─ per-address 60-second mail cooldown
  ├─ instance-wide mail budget
  ├─ OTP / verify / refresh limits
  └─ account and session state
   │
   ▼
SMTP provider
```

每层只承担一个清晰职责：Nginx 在进入应用前削峰；Python 组合终端地址、邮箱和动作执行产品级限制；Turnstile 提高自动化请求成本；GoTrue 保留认证业务冷却和最终邮件预算。任一内层保护失效时，外层仍能限制损失。

## 4. 可信客户端地址

限流准确性的前提是客户端不能伪造地址。

1. 当前 Nginx 直接接收公网连接，以连接来源 `$remote_addr` 为准。它覆盖写入 `X-PSKit-Client-IP`，并清洗客户端提交的同名 Header；不能用未经清洗的 `$proxy_add_x_forwarded_for` 作为限流键。
2. Python 只有在 socket 对端属于配置的可信反向代理集合时才读取 `X-PSKit-Client-IP`。当前生产集合只包含宿主回环代理；其他来源使用实际 socket 地址。
3. Python 使用标准 IP parser 规范化 IPv4/IPv6，再将服务端确定的值覆盖写入调用 GoTrue 的内部 Header。浏览器提供的地址永不透传。
4. GoTrue 显式设置 `GOTRUE_RATE_LIMIT_HEADER=X-PSKit-Client-IP`。Python 到 GoTrue 的所有相关路径必须携带该 Header；Nginx 直接代理的公开 `/auth/v1/verify` 也覆盖写入它。
5. 后续增加 WAF/CDN 时，只对厂商公布的回源 CIDR 配置 `set_real_ip_from`、正确的 `real_ip_header` 与 `real_ip_recursive on`，并在安全组阻止绕过边缘直接访问源站。

详见 [Nginx real IP module](https://nginx.org/en/docs/http/ngx_http_realip_module.html)。

## 5. Nginx 粗粒度限流

只在认证端点使用独立共享内存 zone，不给聊天 SSE、文件上传和普通 API 套用同一严格规则。初始值如下：

| 路由组 | 逐 IP 速率 | 突发 | 说明 |
| --- | ---: | ---: | --- |
| `signup`、`password/recover`、游客邮箱升级发信 | `6r/m` | 3 | 昂贵发信入口，共享一组预算 |
| `login` | `10r/m` | 5 | 缓解密码喷洒，精确账号控制由 Python 完成 |
| `verify`、游客邮箱升级验证 | `30r/m` | 10 | 允许正常输错与多标签页，不允许高速枚举 |

所有拒绝统一使用 HTTP `429`。Nginx 通过受控的 `error_page`/命名 location 把原生拒绝转换为 `{"detail":{"code":"AUTH_RATE_LIMITED"}}`，并添加保守的 `Retry-After: 60`；不能把默认 HTML 429 页面直接交给前端。发信路由另叠加按站点的短时总量保护，建议 `2r/s`、`burst=10`，防止大量轮换 IP 同时进入 Python。`limit_conn` 如启用，只施加在认证路由，初始每 IP 10 条连接；不得影响 HTTP/2 下的聊天流。

上线先启用 `limit_req_dry_run on` 并记录 `$limit_req_status`，观察 24–48 小时真实校园网/移动网流量后再执行拒绝。阈值通过部署环境和模板管理，不在前端或业务代码中写死。

## 6. PostgreSQL AuthAbuseGuard

### 6.1 标识与数据最小化

服务端将规范化邮箱转为小写并去除首尾空白；不做 Gmail 点号、加号别名等提供商专属改写。邮箱与 IP 使用独立命名空间计算：

```text
HMAC-SHA256(rate_limit_secret, "email:" + normalized_email)
HMAC-SHA256(rate_limit_secret, "ip:" + normalized_ip)
```

数据库只保存摘要、动作、窗口、计数和到期时间，不保存密码、验证码、CAPTCHA token 或原始 IP。普通限流日志不记录完整邮箱。所有生产 Python 实例必须使用同一限流密钥；密钥缺失时 live 模式拒绝启动防滥用模块，不能降级为进程内计数。

### 6.2 初始策略

全网开放注册采用以下起点：

| 动作 | 维度 | 限额 | `Retry-After` |
| --- | --- | ---: | --- |
| 注册、找回、游客升级发信 | 同一邮箱 | 1 次/60 秒 | 距冷却结束秒数 |
| 同上 | 同一邮箱 | 5 次/小时 | 当前小时窗口剩余秒数 |
| 同上 | 同一邮箱 | 10 次/天 | 当前 UTC 日剩余秒数 |
| 同上 | 同一 IP | 20 次/10 分钟 | 当前窗口剩余秒数 |
| 同上 | 同一 IP | 100 次/UTC 日 | 当前 UTC 日剩余秒数 |
| OTP 验证尝试 | 邮箱 + IP | 10 次/10 分钟 | 触发后锁定 15 分钟 |
| 密码登录尝试 | 同一账号 | 10 次/15 分钟 | 当前窗口剩余秒数 |
| 密码登录尝试 | 同一 IP | 30 次/5 分钟 | 当前窗口剩余秒数 |

IP 日额度刻意高于邮箱额度，兼容校园网共享出口。OTP 与密码登录在调用 GoTrue 前预占尝试，防止大量并发请求同时穿过尚未增长的失败计数。有效 OTP 或正确密码会幂等退回账号/邮箱失败额度，但保留较宽松的 IP 尝试额度；无效凭据保留全部消耗。只有 provider adapter 能证明请求尚未发出时，才退回账号/邮箱额度；连接写入后超时、读取响应失败和其他结果不确定状态保留预占并记录 `outcome_unknown`，避免实际已执行却因响应丢失而绕过限额。发信的 IP 尝试在 CAPTCHA 前计数，邮箱发信预算在 CAPTCHA 成功后占用，避免攻击者无需通过挑战就锁住受害邮箱。

固定窗口使用数据库唯一约束和原子 upsert；60 秒冷却补足窗口边界可能产生的双倍突发。同一请求适用的冷却、小时、日、IP 和账号 bucket 必须在一个 PostgreSQL 事务或等价的单次存储函数中锁定、检查并占用，任一超限则整体回滚，不能逐个提交造成部分扣额或并发穿透。拒绝返回所有失败规则中最长的有效 `Retry-After`。过期计数异步批量清理，判断正确性不依赖清理作业及时运行。

发信调用 Supabase 前先原子预占邮箱额度。Supabase 明确接受请求、返回业务/限流错误，或请求写入后结果不确定时保留消耗；只有连接建立/请求发送前的确定失败才用同一 claim token 幂等退回应用层邮箱额度。不能通过自动重试不确定请求判断邮件是否发送。IP 尝试预算不退回。

### 6.3 覆盖范围

所有当前和未来会触发邮件的 Python 接口必须经过同一入口，包括：

- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/password/recover`
- `POST /api/v1/auth/upgrade/email`
- 后续增加的重新发送验证码接口

OTP 校验覆盖普通注册、密码恢复和游客邮箱升级。禁止某个新端点直接调用 `identity_provider` 绕过 `AuthAbuseGuard`。

## 7. Turnstile 人机验证

普通邮箱注册、找回密码、游客邮箱升级发信都要求一次新的 Cloudflare Turnstile Managed token。Google OAuth 登录和已登录的普通 API 不弹 CAPTCHA；密码登录第一版由 Nginx 和失败计数保护，后续可以在连续失败阈值前增加自适应挑战。

实现边界：

- 将现有仅供游客使用的 `GuestCaptcha` 抽成认证通用前端组件；站点 key 是公开前端配置，secret 只存在 Python 受限环境文件。本期 Python Siteverify 适用于邮箱发信端点；当前仍关闭的匿名登录及其 GoTrue CAPTCHA 路径不在本期暗中改写。
- Python 调用 Turnstile Siteverify，校验 `success`、预期 `action` 和生产 hostname；可传入可信客户端地址辅助风险判断。
- token 单次使用且约五分钟过期。每次提交结束后无论成功失败都重置组件，禁止复用。
- Siteverify 超时、不可达、token 缺失、过期或 action/hostname 不匹配时 fail closed，不调用 Supabase、不占用邮箱发信预算；IP 尝试仍被记录。
- production 与 staging 使用独立站点配置；自动化测试使用受控替身或官方测试 key，不调用真实 SMTP。
- 本期在 Python 中验证 CAPTCHA，不打开会同时改变多个 GoTrue 路由行为的全局 CAPTCHA 开关。这样可以先只保护昂贵发信接口，并保持现有登录兼容。

Turnstile 不要求域名经过 Cloudflare 代理，但必须服务端验证：[Turnstile server-side validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)。

## 8. GoTrue 显式配置

生产与 staging 都固定写出完整行为，生产初始值：

```env
GOTRUE_SMTP_MAX_FREQUENCY=60s
GOTRUE_RATE_LIMIT_EMAIL_SENT=60
GOTRUE_RATE_LIMIT_OTP=30
GOTRUE_RATE_LIMIT_VERIFY=30
GOTRUE_RATE_LIMIT_TOKEN_REFRESH=150
GOTRUE_RATE_LIMIT_HEADER=X-PSKit-Client-IP
```

`GOTRUE_RATE_LIMIT_EMAIL_SENT=60` 是单个 Auth 实例的全局每小时预算，作为 SMTP 损失上限，不是逐用户限制。GoTrue `v2.196.0` 对裸数字和 `n/duration` 使用不同算法；这里使用裸数字，避免把 `60/1h` 误认为固定每小时 60 次。未来扩容多个 GoTrue 实例时，该内存额度会按实例相乘，因此 Python/PostgreSQL 的共享保护继续作为主约束。

全局发信预算上线后按“正常峰值小时 P99 × 3”和 SMTP 商家允许额度调整，且不低于 60。调整必须记录原因、观测区间和回退值。完成 CAPTCHA、限流和真实 SMTP 验收后，生产设置 `CLOUD_DISABLE_SIGNUP=false` 实现全网开放；Staging 保持独立策略，不能发送真实生产邮件。`CLOUD_DISABLE_SIGNUP`/`GOTRUE_DISABLE_SIGNUP` 保留为攻击或 SMTP 故障期间的紧急注册关闭开关；找回密码是否一并关闭由单独入口规则控制。

版本依据：[GoTrue v2.196.0 configuration](https://github.com/supabase/auth/blob/v2.196.0/internal/conf/configuration.go) 与 [rate parser](https://github.com/supabase/auth/blob/v2.196.0/internal/conf/rate.go)。

## 9. Python API 与公开错误

认证请求 DTO 增加可选 `captcha_token`，live 公网部署按端点策略要求必填；mock 模式保持可测试。服务端统一返回稳定代码：

| 状态 | 代码 | 公开行为 |
| --- | --- | --- |
| 422 | `CAPTCHA_REQUIRED` / `CAPTCHA_INVALID` | 重置挑战并保留邮箱/表单 |
| 429 | `AUTH_RATE_LIMITED` | 携带整数秒 `Retry-After` |
| 503 | `AUTH_CAPTCHA_UNAVAILABLE` | 暂时无法发送，不消耗邮箱额度 |
| 503 | `IDENTITY_UNAVAILABLE` | Supabase/SMTP 前置服务不可用 |

找回密码对存在与不存在的合法邮箱返回相同公开成功响应和相近处理路径，避免账号枚举。内部日志可记录匿名摘要、规则名和上游分类，但不返回 Supabase/SMTP 原始错误正文。注册中邮箱已存在的交互不能泄露额外账号状态；产品可统一引导用户登录或找回密码。

Python 将自身限流的 `Retry-After` 与 GoTrue 返回值统一为正整数秒。Nginx 的 `429` 也应携带合理的短倒计时；如果某层无法提供精确值，前端使用保守默认值，但不能自动重放注册、登录或发信请求。

## 10. 前端行为

- 注册、找回密码和游客邮箱升级表单嵌入同一认证 CAPTCHA 组件，仅在相关模式加载。
- `ResearchApi` 把一次性 `captcha_token` 只发送给 Python；浏览器不直接调用 Supabase Siteverify 或携带 secret。
- `ApiError` 解析并保留可信的整数 `Retry-After`。收到 `429` 后禁用对应提交/重发动作并显示双语秒级倒计时。
- 成功发信也启动至少 60 秒倒计时。当前表单没有重发入口；未来增加时复用同一状态和服务端规则。
- 倒计时可以放进 `sessionStorage` 改善刷新体验，key 按动作与规范化邮箱摘要隔离，不存密码、验证码或 CAPTCHA token。浏览器计时只用于交互，服务端始终具有最终决定权。
- CAPTCHA 失败、过期和服务不可用提供明确可恢复提示；重置挑战时保留用户已填邮箱。密码只保留在当前组件内存中。
- 找回密码界面始终显示“若该邮箱已注册，我们已发送邮件”一类统一文案。

## 11. 监控、告警与数据保留

结构化指标至少包括：

- Nginx auth zone 的 passed/delayed/rejected/dry-run rejected。
- Python 限流按 action、dimension、rule 统计允许和拒绝次数。
- Turnstile 成功、失败、超时和 action/hostname 不匹配。
- GoTrue `429`、邮件请求接受、Supabase/SMTP 错误分类。
- 注册成功率、验证码成功率，以及 CAPTCHA/限流变化前后的转化率。

日志只记录 HMAC 摘要的短前缀用于关联，不记录原始邮箱、IP、密码、验证码、Cookie、JWT 或 CAPTCHA token。连续 5 分钟出现异常拒绝峰值、全局邮件预算接近耗尽、SMTP 错误率升高或 Turnstile 大面积不可用时告警。保留周期与现有应用日志政策一致；过期限流 bucket 最迟 48 小时内清理，日级聚合监控可以单独保留。

## 12. 灰度与回退

1. 先部署指标、可信 IP 解析和 Nginx dry-run，不改变用户请求结果。
2. 显式固定 GoTrue 参数，验证 60 秒冷却、全局预算和内部 Header。
3. 部署 PostgreSQL `AuthAbuseGuard`，先观察计数并对明显攻击规则执行，核对校园网共享出口。
4. 前后端同时部署 Turnstile、`Retry-After` 和倒计时；只有前端已能提供 token 后才在 live 端点强制 CAPTCHA。
5. 观察 24–48 小时后启用 Nginx 拒绝和完整 Python 阈值。
6. 流量或风险增加时接入阿里云 WAF/Anti-DDoS，并封闭源站绕过路径。

每阶段都可以独立回退到上一阶段。回退 CAPTCHA 强制时仍保留 Nginx、Python 限流和 GoTrue 冷却；回退 Python 新版本不能删除限流表。出现误伤时优先提高 IP 阈值，保留严格邮箱冷却。遭遇攻击时可暂时设置 `CLOUD_DISABLE_SIGNUP=true`，保持已有用户登录和管理员私网入口。

## 13. 验收条件

### 后端与并发

- 同一邮箱并发提交注册时，只有一个请求进入实际发信，其他请求得到带 `Retry-After` 的 `429`。
- 两个 Python 实例共享 PostgreSQL 计数，不会各自放行一份额度。
- 同一来源伪造不同 `X-Forwarded-For` 仍落入同一限流桶；只有可信代理可以改变终端地址。
- Nginx 原生拒绝被转换为稳定 JSON `429` 和 `Retry-After`；signup、recover、login、verify 命中各自规则，而 `/runs/*/events`、普通 API、私网管理入口和旧站不命中这些 zone。
- 只有请求确定未发出时邮箱额度才幂等退回；写入后超时、响应丢失或其他不确定结果保留预占并且不会自动重试。
- 同一请求的所有适用 bucket 在一个 PostgreSQL 事务中检查和占用；任一超限时没有其他 bucket 被部分扣减。
- 未知和已存在的找回邮箱得到相同公开响应，不通过状态码、错误码或正文泄露账号存在性。
- GoTrue 调用链携带服务端覆盖的内部 IP Header；缺失时测试失败。

### CAPTCHA

- 缺失、无效、过期、重复使用以及错误 action/hostname 的 token 都不会触发发信。
- Siteverify 超时 fail closed，返回稳定可重试错误，不消耗邮箱发信预算。
- 有效 token 只能支撑一次相应动作，staging 与 production token 不混用。

### 限流与正常流量

- 60 秒、小时、日、OTP 和登录失败窗口的边界及并发原子性均有时钟可控测试。
- 办公网共享 IPv4、移动 IPv6和正常多标签页流程通过压测；Nginx dry-run 数据证明初始阈值没有明显误伤后才启用。
- Google OAuth、Refresh Cookie、普通登录成功、聊天 SSE、上传和 Agent Run 不受发信限流影响。

### 前端与运维

- `429` 倒计时使用服务端 `Retry-After`，刷新页面可恢复体验状态，计时结束前不会自动重试。
- CAPTCHA 重置后保留邮箱，密码/验证码/token 不写入浏览器持久存储。
- Nginx、Python、Turnstile、GoTrue 和 SMTP 的拒绝原因可以通过无敏感信息的指标区分。
- 运维文档说明正常调参、攻击时关闭注册、Turnstile 故障回退和后续接入 WAF 的步骤。
- Compose 渲染测试证明固定 GoTrue 参数实际进入 `auth` 容器；真实容器检查只确认变量是否存在及非秘密值，不打印任何密钥。
- 宿主 Nginx 发布仍先执行 `nginx -t`，失败自动恢复上一配置；后端与 Supabase 端口继续只绑定回环，`/internal/`、公网管理台隔离及旧站路由不回归。

## 14. 本期边界

本期不引入 Redis，不让浏览器直连 Supabase Auth，不将原始邮箱/IP保存为限流键，不承诺本地 Nginx 可以防御带宽型 DDoS，也不实现复杂的行为指纹、设备指纹或机器学习风控。管理员动态编辑限流策略、风险评分和按国家/ASN 策略可以在真实流量出现后另行设计。

本规格确认后再编写 TDD 实施计划；在实施计划审阅通过前，不修改生产认证配置或线上 Nginx。
