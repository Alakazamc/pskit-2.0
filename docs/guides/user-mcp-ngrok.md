# 将自己的内网 MCP 接入 PSKit

日期：2026-10-05。**这是待实现连接页面的操作指南设计**；正式产品尚未上线该自助入口。本轮没有运行隧道、开放端口或创建真实连接。以下域名、端口和凭据均为示例，须替换为本人服务配置。

## 选择连接方式

推荐路径是：

```text
PSKit 云端后端 → 受认证的 HTTPS 地址 → ngrok → 本机 HTTP MCP
```

浏览器有条件地可以访问本机 / 内网 MCP，但需要服务的 CORS 配置和浏览器本地网络权限，并受到 HTTPS、浏览器版本及系统策略影响。浏览器连通不代表云端 Pi 能连通；关闭网页也不能保证浏览器中继继续工作。[Chrome 本地网络访问说明](https://developer.chrome.com/blog/local-network-access)、[CORS](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/CORS)

因此本期采用后端连接 HTTPS 服务。未来可增加本机主动连接器，减少公开入口；当前不需要用户安装另一个 PSKit 守护进程。

## 1. 准备自己的 HTTP MCP

确认 MCP 服务已有 HTTP endpoint，例如 `http://127.0.0.1:8787/mcp`，并启动在 ngrok 所在电脑上。已有 `/predict` REST 接口或只能在终端使用的 stdio MCP，需要先由服务开发者添加 HTTP MCP 适配；ngrok 不会转换协议。[MCP 传输](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)、[stdio](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio)

在服务端配置并验证访问认证：

- 服务 Token 模式：每次请求验证 `Authorization: Bearer …` 的有效内容，不能只检查请求有没有该头。
- OAuth 模式：使用连接客户端支持的 MCP OAuth 流程；不能拿 PSKit 登录 JWT 当作自定义服务的访问凭据。
- 仅向 PSKit 授权所需工具，不默认开放读取整机文件、任意命令或所有模型任务。

先确认缺失 / 错误 / 过期凭据会被拒绝。具体配置取决于你的 MCP 框架，没有一个通用环境变量可以自动给所有服务添加认证。服务也需要接受实际公网 Host / 合法 Origin；不要通过全开放来源或关闭 TLS 验证解决错误。[MCP 授权](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)

注意协议兼容：当前 PSKit 使用 MCP Python SDK v1 系列，远端必须支持其实际协商版本。2026-07-28 与 2025-11-25 的握手、会话及 SSE 规则不同；新版示例不能自动代表旧客户端已兼容。仅需开发者在服务配置 / 连接详情中确认此信息，不要求普通用户手写协议头。

## 2. 安装并私密配置 ngrok

在**运行 MCP 服务的电脑**按[官方下载指引](https://ngrok.com/download)安装 ngrok，并在自己的 Dashboard 查看分配的域名和 agent authtoken。

**两种 Token 用途不同：**ngrok agent authtoken 用于连接 ngrok，保存在你的电脑；MCP 服务 Token 用于访问模型工具，之后填写到 PSKit。不要把 ngrok authtoken 填入 MCP 连接表单。[ngrok v3 配置](https://ngrok.com/docs/gateway/agent/config/v3)

Linux / WSL / macOS 可用下面步骤创建专用私密配置；Windows 使用相应的用户配置路径，并将文件权限限制为本人。实际凭据在本机编辑器中填写，不贴入聊天、URL、截图或仓库：

```bash
umask 077
mkdir -p "$HOME/.config/pskit-mcp"
ngrok config edit --config "$HOME/.config/pskit-mcp/ngrok.yml"
```

配置示例：

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

将域名完整替换成自己的实际值，不要直接复制占位符。当前 ngrok 账号有固定 Dev Domain；自选 / 预留域名依账号计划而定。固定域名不会让关机的电脑继续提供服务。[域名说明](https://ngrok.com/docs/gateway/domains)

## 3. 启动隧道

```bash
chmod 600 "$HOME/.config/pskit-mcp/ngrok.yml"
ngrok config check --config "$HOME/.config/pskit-mcp/ngrok.yml"
ngrok start pskit-mcp --config "$HOME/.config/pskit-mcp/ngrok.yml"
```

如果已安全配置 agent 身份，也可以临时开启：

```bash
ngrok http http://127.0.0.1:8787 \
  --url https://YOUR_ASSIGNED_DOMAIN.ngrok-free.app --inspect=false
```

`--inspect=false` 控制该 HTTP 隧道的 introspection；账号侧的日志、观测与留存需另行确认。这些命令不会给 upstream 自动增加访问认证，第一步的认证必须已生效。[CLI](https://ngrok.com/docs/gateway/agent/cli)、[Traffic Inspector](https://ngrok.com/docs/share-localhost/inspection)

ngrok agent、MCP 服务和电脑须保持在线。本指南只转发专用 MCP 端口；其他数据库、Docker API、整机管理页面不属于该连接。

## 4. 在 PSKit 添加连接

页面上线后：

1. 侧栏 **MCP → 添加连接**，填写名称。
2. 地址填写 `https://你的实际域名/mcp`，保留服务真实路径；并非所有服务都使用 `/mcp`。
3. 选择认证方式，填写 **MCP 服务专用 Token** 或完成受支持的 OAuth 授权。
4. 点 **测试连接**：后端检查握手与工具发现，不执行预测、不启动 GPU 任务。
5. 勾选允许助手使用的工具，再 **保存并启用**。连接只属于本人；平台共享服务另由管理员发布。

启用不代表立即调用，也不会替换输入框选择的聊天模型。关闭连接会拒绝新调用；已提交的任务单独显示实际状态，需要服务确认取消后才能算停止。

## 常见问题

| 现象 | 先核对 |
| --- | --- |
| 401 / 403 | 使用的是 MCP 服务 Token 吗？是否有效？服务是否接受该域名 / Origin？ |
| 404 / 405 | MCP 的真实路径与 HTTP 传输是否正确？仅浏览器 GET 或 HEAD 失败不代表 MCP POST 不能工作。 |
| 返回 HTML / 重定向登录页面 | 网页登录门禁不是 MCP OAuth token 流程；服务需提供客户端支持的认证，不能复用浏览器登录 Cookie。 |
| 浏览器能打开，连接检查失败 | 后端到服务的网络、认证、握手与版本分别检查；不要只凭 HTTP 200 判断。 |
| 本机地址被拒绝 | 云端后端的 localhost 不是你的电脑；填写 ngrok HTTPS endpoint。平台私网服务由管理员配置。 |
| 不可达 / 离线 | MCP 进程、ngrok agent、网络、电脑休眠状态和实际域名。 |
| 能列出工具但长任务丢失 | 工具需有提交确认、Job ID 和查询 / 取消 / 结果协议；进度流本身不是持久任务。 |
| 关闭隧道后 GPU 仍在运行 | 停止网络入口不等于取消已提交计算；使用服务取消接口并确认状态。 |

## 数据与资源的范围

计算仍运行在自己的电脑 / GPU 上。允许工具的请求和返回数据经过 ngrok 与 PSKit；交给 Agent 的结果可能进入所选 LLM 上下文。有效认证、工具范围、传输 / 日志策略决定哪些内容被使用；不能仅凭“使用 ngrok”承诺完全不外泄。[ngrok 数据路径](https://ngrok.com/docs/gateway/how-it-works)

PSKit 只保存本人连接和授权，不自动公开分享；密钥采用只写表单，不回读明文。自有服务报告的 CPU / GPU 时间会标为外部自报，只有被管理员验收为可信平台来源后，才进入共享资源计量。

设计依据：[MCP 连接规格](../superpowers/specs/2026-10-05-user-mcp-connections-design.md)、[官方连通性研究](../research/2026-10-05-user-mcp-connectivity-research.md)。
