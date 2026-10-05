# PSKit 管理台

管理台位于 `https://agent.bioailab.net/admin/models`，只通过 WireGuard 访问：客户端连接 WireGuard 后，把该域名在本机临时解析到 `10.9.8.1`。公网 Nginx 对 `/admin` 和 `/api/v1/admin` 返回 `403`。管理台复用已有 Python 登录接口与 Supabase 身份。浏览器只发送普通用户 JWT；管理角色存放在 PSKit PostgreSQL 中，每次请求重新查询。用户 metadata、前端隐藏按钮和过期的权限缓存均不能授予管理权限。

## 初始化管理员

先让目标用户通过网站登录一次，使后端记录其已验证的正式账号身份。在已加载后端私有环境变量的服务器终端执行：

```bash
python -m app.commands.grant_admin --user-id <verified-user-id> \
  --role platform_admin --reason "Initial platform administrator"
```

撤销使用同一命令加 `--revoke`。命令只接受 live PostgreSQL 环境中的已验证正式账号，记录服务器操作者与审计。`service_maintainer` 需传 `--service-id`；另外支持 `quota_operator` 和 `auditor`。管理员仍受模型授权与正常 Token/GPU 限额约束。

## 模型开放

LiteLLM 负责 provider、API key 和部署，管理台负责 alias 的发布及用户/用户组授权。管理 API 不接收或返回提供商密钥。

- `RESEARCH_AGENT_MODEL_POLICY_MODE=legacy` 是兼容默认值，保持当前模型列表行为。
- `managed` 模式只显示已经发布且当前用户获准使用的 alias。新目录为空；先在隔离环境验证，再修改生产配置。
- 草稿编辑、发布、撤销带 `expected_revision` 与变更理由。空用户和组 allowlist 表示没有任何用户获得授权。
- 发送入口及每一次真正的模型代理调用都检查当前策略；旧请求不能绕过之后的撤权。图片和推理能力只能在 LiteLLM 声明的范围内收窄。

## 科研服务

服务地址和凭据位置由服务器批准，页面填写引用名称。配置示例：

```dotenv
RESEARCH_AGENT_ADMIN_SERVICE_ENDPOINTS_JSON={"lab-predictor":{"url":"http://lab-predictor:8080","transport":"http","health_path":"/health","schema_path":"/schema"}}
RESEARCH_AGENT_ADMIN_SERVICE_CREDENTIALS_JSON={"lab-token":"LAB_PREDICTOR_TOKEN"}
```

MCP 地址配置还需 `allowed_tools`。实际凭据写入服务器私有环境中的 `LAB_PREDICTOR_TOKEN`，不填入草稿 JSON。连接检查与 schema 检查不运行推理；schema 改变不自动修改已发布能力。通过校验的服务进入配置发布包，发布、审计和 outbox 同事务提交；已创建作业保留原能力快照。

完整 HTTP 字段与分页约定见 [管理 API 契约](../docs/contracts/2026-10-04-admin-api.md)。模型提供方的 SDK 和 Completed/Pending/UsageReport 约定见 [计算服务接入](COMPUTE_SERVICES.md)。

## 配额与运行管理

管理页面展示用量、预占和剩余额度。降低限额不会清除已使用或预占记录。并发限制在任务准入事务中生效；上传和沙箱产物共用存储额度，在写入事务中检查。未设置的正式用户存储上限保留 `null`，显式设置 `0` 禁止新增文件。

作业取消先显示“请求停止”，收到实际停止确认后才释放预占；计量不明确的作业进入待对账。通用计算人工对账必须提供终态、停止证据和理由。沙箱排空拒绝新的执行，活动结束后停止容器，保留用户卷。沙箱管理器未配置或失联时页面显示不可用，不返回虚构的空目录。

旧 AF3 任务也显示在管理台中。已领取任务取消后仍保留预占，显示待确认，直到回报或人工核验。AF3 对账沿用原有运行时长分钟口径，保留已有任务终态，须提供停止证据与理由；不把这些分钟转换为通用计算的 GPU 设备毫秒。浏览器使用新增的 `/admin/af3/jobs/{id}/reconcile` 接口；原 `X-Admin-Key` 运维接口保持兼容。

## 数据库与验收

显式执行 PostgreSQL migrations 至当前 `SCHEMA_VERSION` 后再启动新版。v5 保存沙箱所有权、活动租约和产物引用，v6 保存角色、策略、发布和审计。应用启动不会自动升级 schema。

旧 SQLite 导出无法保存这些记录：存在计算、沙箱或管理状态时，导出工具拒绝有损回退，需用 PostgreSQL 备份恢复。不要删除用户卷或生产账单来重置测试。

本地验收使用独立测试数据库，每条测试创建并删除随机 schema，以合成身份访问真实应用 HTTP 边界：

```bash
cd new_backend
export TEST_POSTGRES_DSN='<isolated-loopback-test-database-dsn>'
PYTHONPATH=.:.. .venv/bin/python ../deploy/agent/scripts/admin_smoke.py
```

该脚本拒绝非本机数据库地址，不调用付费模型。React 行为测试通过 HTTP 替身注入契约数据，不在页面内保留示例用户或作业。沙箱真实 Docker 生命周期另由 [沙箱部署验收](../deploy/agent/SANDBOX.md)执行。局部测试通过不等于已部署到远程 Staging 或生产。
