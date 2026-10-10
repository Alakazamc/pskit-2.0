# PinCC 工具兼容层的复验与退役

当前部署固定 `ghcr.io/berriai/litellm:v1.100.3`；`provider_compat.py` 只修改
`https://v2.pincc.ai/v1/messages` 的 Anthropic 自定义工具 `type=custom`。
模块初始化时核对安装版本。源码和镜像版本一起评审，不能通过放宽版本检查
来跳过复验。已有生产真实工具证据见
[2026-10-10 发布记录](../../docs/releases/2026-10-10-pincc-tool-compatibility.md)。

新栈 CI 在固定镜像内运行 `test_provider_compat.py`，使用真实 LiteLLM 转换、
真实 `log_pre_api_call` 回调和 HTTP MockTransport，对 PinCC、官方 Anthropic、
官方 OpenAI 分别覆盖同步/异步、流式/非流式请求。容器使用 `--network none`，
只传入 `offline-only` 占位值。它证明本地转换与回调边界，并不证明供应商在线
接受请求、工具执行或计费行为。

在仓库根目录运行同一离线门禁：

```bash
timeout 180 docker run --rm --network none --read-only --tmpfs /tmp \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/compat -v "$PWD/infra/litellm:/compat:ro" \
  --entrypoint python ghcr.io/berriai/litellm:v1.100.3 \
  /compat/test_provider_compat.py
```

## 升级与退役探针

1. 在独立候选镜像上保存安装版本与镜像 digest。移除回调后，通过 mock transport
   捕获原生 Anthropic 请求，确认候选转换是否仍添加 `type=custom`。如果添加，
   供应商必须另有接受此字段的实际证据；离线检查不能推出上游已修复。
2. 对候选版本运行完整离线对照，确认官方 Anthropic 与 OpenAI 的工具 schema、
   tool choice、工具结果和流式响应均保持原协议。检查回调仍在转换后、HTTP
   序列化前执行。版本不一致造成的初始化失败是门禁失败。
3. 在获授权的 Staging 上，关闭候选路由的兼容回调，使用现有
   `deploy/agent/tests/smoke_workspace_tools_live.py` 做限额真实工具探针。
   验收必须包含 `write_file`、`read_file`、`artifact.created`、完成事件、鉴权下载
   与预览字节一致。记录模型、原生路由、LiteLLM/compat SHA、Run、费用和结果；
   HTTP 200 或文本声称已创建文件不算通过。
4. 只有候选无回调真实探针通过，才提交删除兼容模块、所有 Compose 挂载和
   callback 配置的独立改动，并重复离线门禁及获授权的 Staging smoke。保留旧
   固定镜像和配置作为回滚入口。无法得到真实探针证据时保留兼容层。

本轮 Phase 2 只建立离线门禁和操作入口，没有执行退役或付费供应商探针。
