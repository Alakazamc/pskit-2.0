# PinCC 原生工具协议兼容发布

日期：2026-10-10（Asia/Shanghai）。分支：`codex/new-stack-baseline`。

## 原因与改动

此前 Pi 工具注册、沙箱和下载接口均已存在，但真实模型只输出 `<write_file>`
普通文本。单字段对照确认当前 PinCC 上游不兼容 LiteLLM 添加的
`tools[*].type=custom`，详见 [诊断记录](../diagnostics/2026-10-10-model-tool-protocol.md)。

本次增加 `infra/litellm/provider_compat.py`，在固定 LiteLLM 版本的同步原生
请求回调中，限定 `anthropic` 提供商与 `https://v2.pincc.ai/v1/messages`
（默认端口或 443），只删除自定义工具的这个可选字段。其他工具类型、
schema、名称、缓存标记和其他提供商保持不变。未将模型文本解释成执行指令。

模块通过只读挂载和 callback 配置启用，无数据库迁移或模型密钥重配。
回调依赖已核对的固定版本请求字典语义；升级 LiteLLM 必须重新验证，
不是跨版本通用协议插件。

## 制品与范围

按用户要求先部署、后提交。部署源基线为 `0962f12`，增量由服务器上的
`source-manifest.json` 逐文件记录旧/新 SHA256；待提交源码未混入其他工作区改动。

- 发布目录：`/home/ecs-user/pskit-agent-releases/20261010-pincc-tools`。
- Source manifest SHA256：`da1d6bf2a339959037e669fa7736bb5c76823c4ae7b511d426e692af0b39fa76`。
- 兼容模块 SHA256：`c80fbefba35eced87e0975123db5714ec6d421e09d0196392218e26d3f07b9bb`。
- LiteLLM 镜像仍为 `ghcr.io/berriai/litellm:v1.100.3`，ID
  `sha256:607356a5d927cd451bdda228351c363e262e1b40dcd9532b679cdd3ef59e5643`。
- 生产与 Staging gateway 都启用同一模块。后端镜像仍为
  `pskit-agent-backend:20261010-e1a8caf`，ID
  `sha256:781d6dbc3b12f657d10e5038db26ca78daa98fc8296824aea025361782e15757`。
- 前端 index SHA256 仍为
  `45f05e600e65cd9559c11620d4597a7c7648ebee422cba3f5596639848963ae3`。

升级前将服务器文件与 Git 源基线核对，保存旧配置；程序内比较新旧 resolved
Compose，确认差异只有新增模块挂载，未输出其中的密钥。先在私网 4001
候选网关完成真实模型联调，再更新 Staging，最后更新生产网关。候选网关
使用现有模型路由数据库，因此真实请求计费记入生产网关的测试 key；
会话、文件、用户和账号额度仍完全在 Staging。

切换前生产 Run、Job、workspace attempt 均无非终态记录；暂停 backend
后再次检查，然后更新 gateway。网关健康后启动原 backend 容器。没有
重建前后端镜像，没有更新 Nginx、A6000、AF3 回调代理或数据库服务。

## 验证

### 不联网协议检查

在精确的网关镜像中运行 `infra/litellm/test_provider_compat.py`，容器无网络、
无凭据。3 项测试通过，包含 8 种真实 LiteLLM HTTP 序列化路径：
同步/异步 × 流式/非流式 × PinCC/官方 Anthropic。确认实际线上请求正文
中的字段被定向修改；原始工具对象、工具选择、schema 和官方服务请求不变。

### 候选网关的真实 Pi / OpenSandbox 链路

- 模型：`anthropic/claude-sonnet-4-5-20250929`。
- Session：`db9a7a36-624f-4a60-9adc-9ca26b6c7182`。
- Run：`36e41274-aae7-47d4-a5ad-df6fa870d21b`。
- 工具顺序：`write_file` 完成 → `read_file` 完成 → `artifact.created` → `run.completed`。
- 文件：`tool-check.md`，57 bytes；鉴权下载和 Markdown 预览均与预期内容逐字节一致。
- SHA256：`783bbe3b7d4528c017a95c63f5d8e606c176555f240cc47f04fe8a7b74eadfd3`。
- 测试 key 费用快照：$0.011091，预算上限 $0.25、单模型、20 分钟有效。

第一次链路检查已成功写入文件，但下一次模型请求被合成账号原有 20,000
Token 额度限制，未计为完整通过。随后临时增加该合成账号的测试额度并
在 `finally` 恢复。中间一次额度 API 请求因遗漏必填 GPU 字段返回 422，
没有修改额度；补齐原值后完成上述成功验收。

### 正式网关复验

部署后通过同一 Staging 合成账号和独立限额测试 key，经生产 `4000` 网关
重复完整检查，包含持久化聊天消息的 `artifact` part、产物面板列表、下载、
预览和未登录下载拒绝，全部通过：

- Session：`5ac88160-14af-46f9-9a03-2f88a2fa3333`。
- Run：`756363ce-32ec-424c-a96a-c8c29684b2a3`。
- 工具：`write_file`、`read_file` 均 completed；最终 `run.completed`。
- Artifact：`sandbox-0408ceed83789c287174aac52a3628500a08d625429aaf9e5ccb9755465aea77`。
- 聊天历史包含同一 Artifact ID 的类型化 `artifact` part。
- 下载 57 bytes，SHA256：`550f9e667950e3a04ca7d0370cbf5611626a210f98d39ac7ec965ed25bb4be0a`。
- 测试 key 费用快照 $0.0110835；调用结束后临时路由、虚拟 key 已撤销，
  合成账号额度已恢复。私网 4001 临时候选网关随后移除。

生产/Staging readiness 200，工作区 `WORKSPACE_READY`、`runtime=runsc`、
文件能力可用。两个 gateway healthy、restart count 0，容器内模块哈希
与源快照一致。公网 `/login` 为 200，未登录 `/api/v1/usage` 为 401，
`/internal/` 为 404。未触发科研计算或 GPU 任务。

本次验证覆盖真实工具、文件、鉴权 API 和富 UI 所需元数据；未重新进行
浏览器像素级截图验收。前端渲染代码沿用先前已发布版本。旧伪文本回复没有
创建文件，需要重新发起文件生成请求，不会被自动当作历史产物导入。

## 回滚

旧配置及 Compose 保存在发布目录 `previous/infra/litellm/`，不包含 env
或提供商凭据。回滚前等待活动对话结束，短暂停止 backend 接收新请求；
将 `config.yaml` 和 `compose.shared-postgres.yaml` 从该目录原子恢复到
`/home/ecs-user/pskit-agent-cloud-20261002/infra/litellm/`，再运行：

```bash
BASE=/home/ecs-user/pskit-agent-cloud-20261002/infra/litellm
docker compose --env-file "$BASE/.env.shared" \
  -f "$BASE/compose.shared-postgres.yaml" -p pskit-agent-litellm \
  up -d --no-deps --wait gateway
docker start pskit-agent-cloud-backend-1
```

需要回滚 Staging 时也恢复其 `config.staging.yaml`、`compose.staging.yaml`，
使用现存 Staging env、实际 model-stub 镜像和原 Compose 项目更新 gateway。
不回滚数据库或卷，不删除现有文件；回退会重新出现该上游的工具兼容问题。
