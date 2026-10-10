# PSKit LiteLLM 网关

LiteLLM 运行在阿里云 WireGuard 地址 `10.9.8.1:4000`，PostgreSQL 只在 Docker 网络内。管理员通过 `http://10.9.8.1:4000/ui` 自行添加多个模型和提供商 API key；提供商密钥只保存在 LiteLLM 数据库中，不交给 PSKit。`.env` 里的 master key 是 Admin UI 密码，salt key 用于加密数据库里的提供商密钥，**不要重置 salt key**。

## 单 PostgreSQL 候选网关

`compose.yaml` 是旧的独立 PostgreSQL 16 部署，保留给回退使用。新的 `compose.shared-postgres.yaml` 只有 LiteLLM gateway；它通过 `pskit-agent-supabase_default` 网络访问 Supabase PostgreSQL 17 中独立的 `litellm` 数据库。候选阶段旧网关仍使用 4000，新网关只绑定 WireGuard 的 4001。模型、提供商 API key 和旧虚拟 key 不从旧库迁移。

先由统一启动入口运行 `deploy/agent/scripts/provision_shared_postgres.py`，为 `litellm` 和 `pskit_app` 建立不同账号。脚本从受限环境读取 `SHARED_POSTGRES_ADMIN_DSN`、`LITELLM_DB_PASSWORD`、`PSKIT_DB_PASSWORD`；不要把 DSN 或密码放在命令参数中。新网关 `.env` 应与旧网关的 `.env` 分开保存，权限 0600。

候选网关使用：

```bash
cd infra/litellm
docker compose --env-file /path/to/private/candidate.env \
  -f compose.shared-postgres.yaml -p pskit-agent-litellm-candidate up -d --wait
python3 bootstrap_pskit.py --base-url http://10.9.8.1:4001 \
  --env-file /path/to/private/candidate.env \
  --key-file /path/to/private/.pskit-candidate-virtual-key
```

在候选 UI `http://10.9.8.1:4001/ui` 重新添加并测试模型。新 key 文件必须独立于旧 `.pskit-virtual-key`，保存为 0600；重复运行 bootstrap 时会先向指定网关验证已保存的 key。候选模型调用、工具调用、流式响应和预算都通过后，切换期间停旧网关，以同一新数据库和固定镜像在 4000 启动正式网关；旧 PostgreSQL 16 卷保留。

## 旧独立部署的启动方式

```bash
cd infra/litellm
cp .env.example .env
# 设置强随机 DB 密码、master key、salt key；阿里云上设置 LITELLM_BIND_IP=10.9.8.1。
chmod 600 .env
docker compose --env-file .env up -d --wait
```

打开 `http://10.9.8.1:4000/ui`，用户为 `admin`，密码为 `.env` 中的 `LITELLM_MASTER_KEY`。在 **Models + Endpoints → Add Model** 分别添加部署和提供商密钥，并用 **Test Connect** 验证。相同的公开模型名可以对应多个部署，以供网关路由；不同模型也可以使用不同名称。数据库和 `STORE_MODEL_IN_DB=True` 让这些设置持久化。此地址只绑定 WireGuard；它不是公网入口。

## PSKit 预算和密钥

运行 `python3 bootstrap_pskit.py`，建立 `pskit-member-monthly` 默认用户预算：每 30 天 **2 美元**；建立 `pskit-lab` 团队预算：每 30 天 **10 美元**，并把团队虚拟 key 保存到权限为 0600 的 `.pskit-virtual-key`。脚本可重复执行，已保存的 key 只有经当前网关验证有效才会复用。只把团队虚拟 key 交给 PSKit 后端；不要把 master key 交给 PSKit 或浏览器。PSKit 的 Token 月额度仍单独生效，AF3 的 GPU 配额仍由 PSKit 管理。

PSKit 后端设置：

```dotenv
MODEL_GATEWAY_BASE_URL=http://10.9.8.1:4000/v1
MODEL_GATEWAY_MODEL=claude-opus-4-8
MODEL_GATEWAY_KIND=litellm
MODEL_GATEWAY_API_KEY=<团队虚拟密钥>
```

当前部署按用户选择使用 `claude-opus-4-8`。在 Admin UI 中添加并测试同名模型后再切换后端。PSKit Agent 运行时目前按部署配置使用一个默认模型；LiteLLM 可以管理更多模型和密钥，但要让最终用户在 PSKit 页面动态选择模型，还需要另做产品功能。

Pi 的 OpenAI 兼容请求会包含 Anthropic 不支持的 `store=false`。网关配置了 `litellm_settings.drop_params: true`，让 LiteLLM 过滤所选提供商不支持的字段；更换模型后应在 Admin UI 测试工具调用和流式输出，因为过滤掉不支持的字段也可能改变这些能力的表现。

### PinCC 的原生工具协议兼容

当前固定的 LiteLLM `1.100.3` 会在转换后的 Anthropic 自定义工具中添加
`type: custom`。`https://v2.pincc.ai/v1/messages` 上游遇到该字段时会返回普通
文字而不调用工具。`provider_compat.py` 通过配置中的同步 `log_pre_api_call`
回调，仅对这个 HTTPS 主机、端口和路径的 Anthropic 请求移除自定义工具的
该字段；工具名称、参数 schema、缓存标记、系统工具和其他提供商均保留。
此兼容层不会把模型输出的 XML 或代码文本当作工具执行。

回调使用固定版本中“转换后、序列化前”的原生请求字典；这是经过源码与
实际 HTTP transport 验证的行为，不应假定其他版本也一样。版本不匹配时
模块拒绝初始化。升级 LiteLLM 前先在新版本上重新验证并更新版本约束。
Compose 必须同时挂载配置与 Python 模块；生产、Staging 及旧回退 Compose
均已声明该挂载。

不联网的协议验证（同步/异步 × 流式/非流式，包含官方地址不受影响）：

```bash
docker run --rm --network none --read-only --tmpfs /tmp \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/compat -v "$PWD/infra/litellm:/compat:ro" \
  --entrypoint python ghcr.io/berriai/litellm:v1.100.3 \
  /compat/test_provider_compat.py
```

真实联调使用 `deploy/agent/tests/smoke_workspace_tools_live.py`：只登录既有
Staging 合成账号，签发单模型、20 分钟、最多 $0.25 的临时虚拟 key，验证
`write_file`、`read_file`、`artifact.created`、鉴权下载及预览字节一致，最后
撤销临时路由和 key。仅 `message.delta` 或 HTTP 200 不能证明文件创建成功。
候选阶段支持私网 `4001`，上线后用同一工具验证生产网关 `4000`；账号和
文件仍在 Staging。模型调用费用计入生产网关的独立测试 key。

PSKit 从 Run 归属查出用户 ID，向 LiteLLM 发送 `user` 和 `x-litellm-end-user-id`；浏览器和 Pi 提交的身份不会用于记账。费用按 LiteLLM 的模型价格估算，可能与提供商最终账单略有差异。首次真实调用后，应在 Admin UI 的用量页核对团队和用户消耗。

参见 [LiteLLM Admin UI 快速入门](https://docs.litellm.ai/docs/proxy/docker_quick_start)、[预算](https://docs.litellm.ai/docs/proxy/users)和[虚拟密钥](https://docs.litellm.ai/docs/proxy/virtual_keys)。
