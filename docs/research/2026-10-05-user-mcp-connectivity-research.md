# 用户 MCP 连接页：浏览器、内网、远端协议与 ngrok 研究

日期：2026-10-05。仅核对官方文档/源码并提出设计建议；未修改实现、部署、运行隧道或进行收费测试。以下命令供后续指南使用，未在本次执行。

## 结论

本期推荐 **浏览器 → PSKit 后端 → 用户配置的受认证 HTTPS 远端 MCP**。平台 MCP 的用户开关控制该用户是否使用服务；自定义 MCP 归用户所有，凭据由后端安全保存，连接与发现检查在实际执行侧进行。浏览器直连 localhost/LAN 可以作为后续交互模式研究，但不能据此承诺云端 Pi 或离线任务也能连通。

用户内网 MCP 可借助 ngrok agent 的出站连接提供 HTTPS endpoint。它新增了远端访问入口与第三方数据路径；认证、授权、日志配置和本地服务在线状态仍决定暴露范围，不能承诺“资源永不外泄”。后续可研究只接受 PSKit 身份与授权请求的本地 agent，保持浏览器关闭后仍在线。

## 1. HTTPS 浏览器页面访问 localhost/LAN：有条件可行

| 条件 | 官方事实 | 设计影响 |
|---|---|---|
| 网络位置 | localhost/loopback 指当前设备，LAN 地址随所在网络变化。 | 用户浏览器能访问的地址不一定是云端可访问地址。[MDN 地址空间](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access) |
| CORS | 跨源请求受 CORS 限制；JSON POST、Authorization 和协议自定义头通常需要 OPTIONS 预检。带凭据时不能使用通配符 origin。 | MCP 服务须允许确切的 PSKit 页面 origin、方法及 SDK 所需 headers，必要时 expose 协议响应头。`mode: no-cors` 的不透明响应不适合读取 MCP JSON/SSE。[CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS) |
| Mixed content | HTTPS 页的普通 HTTP fetch 可能被阻止；loopback 有可信本地来源规则和实现差异。 | 不应把 localhost 与任意 LAN HTTP 都当作普通公网 HTTP，也不能承诺所有浏览器均放行。[Mixed content](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Mixed_content) |
| Chrome | Chrome 142 引入 Local Network Access 权限，限安全上下文；授权后可对已识别的本地请求放宽 mixed content。私有 IP、`.local`、支持的 `targetAddressSpace` 标注影响判定。旧 PNA 预检方案已被 LNA 替代。 | 不能写“HTTPS 网页绝对不能访问内网 HTTP”。用户仍需授权，CORS/认证/TLS 问题仍需分别解决。[Chrome 官方说明](https://developer.chrome.com/blog/local-network-access)、[142 发布说明](https://developer.chrome.com/release-notes/142) |
| Firefox/Safari | Firefox 官方已有 LNA 安全策略，策略文档标注普通 Firefox 自 145 可用；WebKit 仍有 loopback mixed content 差异的官方跟踪记录。 | 按目标版本、设备和企业/OS 策略验收；不能沿用“只有 Chrome 有限制”或“Safari 必定可行”的断言。[Firefox 策略](https://firefox-admin-docs.mozilla.org/reference/policies/localnetworkaccess/)、[WebKit 跟踪](https://bugs.webkit.org/show_bug.cgi?id=171934) |

当前 MDN 将权限分为 `local-network` / `loopback-network`，保留旧 `local-network-access` alias；具体 API 支持随版本变化。建议检测能力并显示权限修复提示，不把一个固定权限名当作跨浏览器保证。[当前 LNA 文档](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access)

浏览器诊断应区分 DNS/路由、TLS、mixed content、LNA 拒绝、CORS、MCP 认证和协议不兼容。跨源失败常只给脚本笼统错误；“网页可打开”或浏览器探测成功不应写成“云端可用”。

## 2. 云端 Pi 与 durable 任务的边界

Python socket 从运行进程所在机器连接目标地址；HTTP 请求没有因用户浏览器连通而自动获得内网路由的机制。由此推断：云端 Pi 中的 `localhost` 是云端自身，用户 LAN 地址也不会自动指向用户网络。须有公网 HTTPS、VPN/专用网络或明确的反向连接。[Python socket.connect](https://docs.python.org/3/library/socket.html#socket.socket.connect)、[地址空间](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Local_network_access)

浏览器页面会被冻结、丢弃或终止，定时器与 fetch callbacks 可能停止；浏览器中继不能承诺用户关闭页面后持续执行。[Page Lifecycle](https://developer.chrome.com/docs/web-platform/page-lifecycle-api)

设计建议：

- 页面关闭与云端 Run 生命周期分开；执行者必须是持续运行的后端/worker，而非页面回调。
- 本地电脑、MCP 服务或 ngrok agent 关机/断网，云端就无法继续新请求。固定域名不等于固定可用性。
- durable Job 必须有应用级提交确认、Job ID、幂等键、状态/结果查询、取消确认和恢复策略。普通 `tools/call` 加 SSE 不是持久任务账本。
- 浏览器或隧道断开时，不应凭断线推断业务任务已取消、未执行或可安全重试；副作用结果不明时查询/对账。
- 新版 MCP 的断流取消义务针对该协议请求；已提交的 durable Job 是否停止仍由应用级取消与状态确认决定。浏览器订阅流和后端持有的 MCP 请求流应分别管理。
- OAuth 离线访问依赖有效授权与可用刷新机制；刷新 token 并不保证一定被签发，也不让离线的本地服务上线。[MCP refresh tokens](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization#refresh-tokens)

## 3. MCP 远端协议与版本

**本地 stdio：** client 启动子进程，通过 stdin/stdout 交换消息。它是进程/字节流配置，不是可直接填写的 HTTPS URL；已有 stdio server 需要 HTTP wrapper 或本地 agent 做适配。ngrok HTTP 转发本身不会把 stdin/stdout 自动变成 Streamable HTTP。[stdio 规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)

**Streamable HTTP：** 远端 MCP endpoint 接收 POST，响应可以是 JSON 或 SSE，client 须处理两者。服务端须校验 Origin；存在且无效的 Origin 返回 403。本地服务建议仅绑定 loopback，并应使用认证。CORS 是浏览器响应读取策略，Origin 校验防 DNS rebinding，二者与业务授权不能互相替代。[远端传输规范](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)

**Auth：** 当前 HTTP authorization 规范使用 OAuth 资源发现与访问 token；每个 HTTP 请求带 `Authorization: Bearer`，token 不放 URL query，服务端验证有效性及 audience。用户为 MCP 提供的 token 与 PSKit JWT、ngrok agent authtoken 各自独立；PSKit JWT 不透传给任意自定义 endpoint。静态 bearer 支持若另行设计，应标为自定义凭据模式，不宣称完整 OAuth 客户端已实现。[Authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

**需要显式兼容版本：** 2026-07-28 已正式发布，移除了旧 GET stream 与 protocol-level sessions；请求携带新协议 metadata/headers，关闭该请求的 SSE response stream 即为取消信号。2025-11-25 的初始化、session、GET SSE 与恢复规则不同。连接页应显示实际 SDK/服务支持版本，而非用“Streamable HTTP”名称掩盖差异。[正式发布](https://blog.modelcontextprotocol.io/posts/2026-07-28/)、[新版行为](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)、[2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

**老 HTTP+SSE：** 2024-11-05 的 SSE+独立 POST transport 已被取代。可作为显式 legacy 模式支持；按 SDK/规范的回退逻辑处理，不能把新协议中的 SSE 响应当作老 SSE transport。遇到 401/403 不应通过回退或去掉认证来“解决”。[旧版兼容规则](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports#backwards-compatibility)

## 4. ngrok 认证、域名与数据路径

ngrok agent 在本地服务旁运行，出站连接 ngrok 网络的 TLS 443，访问 endpoint 的流量沿连接返回本地；无需为 agent 开放入站端口。HTTP endpoint 仍可在互联网被寻址，保护来自有效访问策略。[Agent 机制](https://ngrok.com/docs/gateway/agent)、[Gateway 数据路径](https://ngrok.com/docs/gateway/how-it-works)

| 项目 | 支持范围与注意事项 |
|---|---|
| Agent authtoken | 认证 agent→ngrok，保留在用户本地私密配置/凭据管理中；不是 MCP endpoint bearer，也不是 PSKit 浏览器凭据。[v3 config](https://ngrok.com/docs/gateway/agent/config/v3) |
| Endpoint bearer | 必须真正验证 token；可由 MCP 服务验证，或使用 ngrok JWT Validation 检查签名、issuer、audience 等。JWT 验证不等于接受任意 opaque API key。[JWT Validation](https://ngrok.com/docs/gateway/traffic-policy/actions/jwt-validation) |
| OAuth/OIDC gate | ngrok 可先让访客登录，再按身份限制访问。OAuth action 未认证时会重定向并使用登录 cookie；该网页登录 gate 不等于 MCP OAuth token 协议。云端 MCP client 不会因用户浏览器已登录自动获得其 cookie/token。[OAuth behavior](https://ngrok.com/docs/gateway/traffic-policy/actions/oauth)、[身份限制](https://ngrok.com/docs/share-localhost/auth) |
| 域名 | 当前每个账号有固定 Dev Domain，免费计划只能使用自动分配的该域名；自选/预留域名需要相应付费计划。用明确的域名 URL 可避免重启时随机换 URL，但服务仍依赖本地 agent。[Domains](https://ngrok.com/docs/gateway/domains) |
| 检查与留存 | Traffic Inspector 能查看请求/响应并重放，包含正文和 headers。应按数据要求关闭不必要检查、配置脱敏与保留；HTTPS 及关闭一个检查选项都不构成“零泄露”的证明。[Traffic Inspector](https://ngrok.com/docs/share-localhost/inspection) |

官方 ngrok MCP 示例用 `hasReqHeader('Authorization')` 拒绝缺头请求，并限制 Anthropic 来源 IP。**从规则可推断它未验证 bearer 内容**；PSKit 不应原样复制为认证方案，也不能照搬 Anthropic 专属 IP 规则。须补有效 token 验证，并验收错误/过期 token 被拒。[MCP 示例及规则](https://ngrok.com/docs/using-ngrok-with/using-mcp)

### 可执行指南建议：已受认证的本地 HTTP MCP → ngrok

前提：用户自己的 MCP 已提供受保护的 Streamable HTTP，例如监听 `127.0.0.1:8787/mcp`；确认缺失、错误及过期 token 被拒。若目前仅为 stdio，先按所用 MCP 框架建立 HTTP adapter，不能直接执行以下步骤后假定协议已转换。

1. 按[官方下载指引](https://ngrok.com/download)安装 CLI，在 ngrok Dashboard 查自己的 Dev Domain 或已获授权使用的域名。
2. 在本地创建私密配置。下面没有真实凭据；在编辑器中替换 agent authtoken，避免 token 进入 shell history、URL、仓库或截图。不要将此 token 填入 PSKit。

```bash
umask 077
mkdir -p "$HOME/.config/pskit-mcp"
ngrok config edit --config "$HOME/.config/pskit-mcp/ngrok.yml"
```

```yaml
version: 3
agent:
  authtoken: REPLACE_LOCALLY_WITH_AGENT_AUTHTOKEN
endpoints:
  - name: pskit-mcp
    url: https://YOUR_ASSIGNED_DOMAIN.ngrok-free.app
    upstream:
      url: http://127.0.0.1:8787
```

3. 检查配置并启动；只有 agent 持续运行时本地 upstream 才可访问。这里域名为占位符，必须按 Dashboard 实际值完整替换。[v3 配置](https://ngrok.com/docs/gateway/agent/config/v3)、[CLI](https://ngrok.com/docs/gateway/agent/cli)

```bash
chmod 600 "$HOME/.config/pskit-mcp/ngrok.yml"
ngrok config check --config "$HOME/.config/pskit-mcp/ngrok.yml"
ngrok start pskit-mcp --config "$HOME/.config/pskit-mcp/ngrok.yml"
```

也可使用已安全配置 agent 身份的 CLI 临时转发：

```bash
ngrok http http://127.0.0.1:8787 --url https://YOUR_ASSIGNED_DOMAIN.ngrok-free.app --inspect=false
```

`--inspect=false` 控制 HTTP introspection；还须单独核对账号/云端的观测与留存设置，不能把它解释为链路中所有参与者均看不到内容。[CLI flag](https://ngrok.com/docs/gateway/agent/cli)

4. 在 PSKit 连接页填 `https://实际域名/mcp`，选择实际 transport/version，填写 **MCP endpoint 专用**凭据或完成受支持的 OAuth 授权。后端保存密文/secret reference，页面仅显示已配置状态；编辑时空值不应意外覆盖原凭据。
5. 从 PSKit 后端测试认证与协议、读取 tools/list；检查无凭据、错 token、协议不匹配、agent 停止等失败。浏览器打开链接或无认证 curl 返回某个错误码，不足以证明有效认证和完整 MCP 调用已经验收。检查阶段不运行模型推理或有副作用工具。

以上启动命令依赖 upstream 已正确保护。若用 ngrok JWT Validation/Traffic Policy 在边缘保护，先按可信 issuer/JWKS/audience 配好策略再开放；能力与费用按用户账号实际计划核对。网页 OAuth gate 若导致 JSON-RPC 收到 HTML/302，应显示认证不兼容，不循环重试。

## 5. 连接页与后续本地 agent 的建议

以下为 PSKit 设计建议，尚未实施：

- **平台服务：** 列表由后端发布并受当前用户权限过滤；个人开关只影响个人工具发现/执行选择，不停止共享 MCP 服务、不取消已提交 Job。管理员全局停用另有权限与审计。
- **用户服务：** owner-private connection；记录名称、URL、transport、凭据方式、启用状态、检查时间与具体失败原因。工具名以 connection ID 命名空间隔离，防覆盖平台工具；可用工具与权限按执行时重新检查。
- **检查结果：** 区分浏览器连通与执行侧连通、认证失败、连接离线、协议/Schema 失败。成功发现不自动调用工具，也不代表工具可信；对长任务明确是否支持 Job 查询/恢复。
- **后端代理边界：** 自定义 URL 属不可信输入，验证 HTTPS/TLS、DNS/重定向目标，阻止访问云端 loopback、私网管理面、metadata 等非授权地址；限制响应大小、超时与并发，凭据不跨 origin 重定向发送。OAuth metadata URL 同样须检查，不能只检查最终 MCP URL。[官方 SSRF 与 token 安全建议](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
- **轻量本地 agent：** 技术上适合后续阶段。ngrok 已证明“本地 side process + 出站 TLS + 云端回传”的可行形式；PSKit 自有方案可仅代理预先批准的本地 MCP（或本地启动的 stdio 进程），不需要重建模型执行环境。[Agent 模式](https://ngrok.com/docs/gateway/agent)
- **自有 agent 必要契约：** 用户/设备配对、短期可撤销凭据、明确允许的本地 endpoint/命令、每次请求授权、心跳、last-seen、并发/流控、重连幂等、断线结果不明与取消确认。可用单个出站 WSS/HTTPS 通道，具体协议需另行设计验证；不开放通用任意 URL/命令代理，也不承诺设备离线时执行。

本期不将浏览器中继或自有 agent 放入 durable 执行关键路径。界面可先提供“HTTPS 远端 MCP + ngrok 指南”；后续若增加浏览器直连，应标明“需要页面保持在线”的模式及其使用边界。

## 验证边界

仅检索官方资料，未运行浏览器/SDK/隧道实测。2026-07-28 与旧 MCP SDK 的差异、Chrome/Firefox/Safari/OS 组合、ngrok 账户功能与上线地区可达性，均须在实施验收时实际验证；本研究不保证网络可达、零数据外泄或后台任务永不中断。
