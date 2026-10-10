# Markdown 文件未生成：模型上游工具协议诊断

日期：2026-10-10（Asia/Shanghai）。本次范围为定位生产对话中的伪工具文本；未修改生产路由或发布新版本。

后续修复状态：定向协议兼容已部署并通过真实 Pi 写入、读回、下载及聊天
产物元数据验证，详见 [修复发布记录](../releases/2026-10-10-pincc-tool-compatibility.md)。
下文保留修复前的诊断过程。

## 已确认的原因

生产 LiteLLM `v1.100.3` 将 OpenAI 格式的 function tool 转成 Anthropic 格式时，会在工具对象中加入 `"type": "custom"`。当前 `v2.pincc.ai` 上游的 `claude-sonnet-4-5-20250929` 路线在该字段存在时没有正常返回工具调用。

同一模型、同一输入、同一原生 `/v1/messages` 接口、相同工具 schema 和强制 `tool_choice`，只改变工具的 `type` 字段，得到：

| 请求 | 上游原始响应 | 结论 |
| --- | --- | --- |
| 保留 `"type": "custom"` | 只有 `text`，`stop_reason=end_turn`；声称没有诊断工具 | 无真实工具调用 |
| 移除这个字段 | `tool_use`，工具名 `diagnostic_echo`，`stop_reason=tool_use` | 返回真实工具调用 |

原生无 `type` 请求两次成功；保留该字段的网关、独立 LiteLLM、原样重放及单字段对照均出现纯文本响应。该结论限定于本次实际配置的上游路线，不能推定所有 Anthropic 兼容服务或其他模型都存在此问题。

## 生产运行证据

- 后端镜像：`pskit-agent-backend:20261010-e1a8caf`；源码基线 `0962f12`。
- 截图对应 workspace attempt：`workspace-7111ec409015401caa9f567436542781`。
- Run：`6fe613f8-7f07-49cb-a003-1daf8bf5ff4b`；Session：`a3971eb8-b082-4ba4-9f32-b5d91185d03b`。
- 模型别名：`anthropic/claude-sonnet-4-5-20250929`；由 LiteLLM 的 `anthropic/*` 部署提供。
- 该 Run 只有 `message.start`、64 个 `message.delta`、`message.end` 与 `run.completed`，没有工具或产物事件。
- Pi 转录中的 assistant 内容为 `thinking` 与 `text`，包含普通文本 `<write_file>`，没有 `toolCall`。
- workspace attempt 无 provider process ID。`completed` 表示本轮结束，不能证明文件写入成功。
- 生产 readiness 显示 OpenSandbox `WORKSPACE_READY`、`runtime=runsc`、`file_access=true`、`command_execution=true`，策略开启。
- 生产 Pi extension 和 system prompt SHA256 与本地一致；没有旧版本残留证据。

## 逐层验证

1. 本地真实 Pi 加载生产 extension，向本机模型替身发送请求；实际请求含 `read_file`、`write_file`、`edit_file`、`list_files`、`find_files`、`search_files`、`bash`、`python`、`update_plan`。
2. 在生产后端容器内用临时目录和本机替身重复相同工具清单检查，结果一致。该检查不使用用户会话或真实模型。
3. 经生产 LiteLLM 发送合成工具 `diagnostic_echo`，强制调用该工具，得到 HTTP 200，但没有 OpenAI `tool_calls`。
4. 通过同一上游的原生 Anthropic 接口发送不含 `type` 的工具定义，返回 `tool_use`。
5. 在独立进程复用已安装 LiteLLM 与当前模型配置，捕获实际 HTTP 正文和原始 SSE：`tools`、`input_schema`、`tool_choice` 均保留，但加入了 `type=custom`；上游 SSE 本身没有 `tool_use`，排除了 Pi 或 LiteLLM 响应解析丢失。
6. 原样重放该 HTTP 正文，仅改用普通 HTTP 客户端请求头，仍无工具调用。
7. 最后用完全相同的请求头和正文做单字段对照：移除 `type=custom` 成功，加入后失败。

合成提示词：`Call diagnostic_echo with value pong. Do not reply with text.`

两组请求均设置：

```json
{
  "model": "claude-sonnet-4-5-20250929",
  "max_tokens": 128,
  "stream": true,
  "tool_choice": {"type": "tool", "name": "diagnostic_echo"},
  "tools": [{
    "name": "diagnostic_echo",
    "type": "custom",
    "description": "Protocol check only; returns the supplied string.",
    "input_schema": {
      "type": "object",
      "properties": {"value": {"type": "string"}},
      "required": ["value"],
      "additionalProperties": false
    }
  }]
}
```

对照仅删除 `tools[0].type`。本次共发出七个合成模型诊断请求，每次请求的输出上限设为 128 Token；未执行模型返回的诊断工具，也未触发科研计算。模型凭据仅在原服务器容器内用于现有目标服务，未导出或写入本报告。

## 修复位置与验收要求

应在该上游的 Anthropic 请求兼容层处理 `tools[*].type=custom`，或由上游修复此字段的支持。兼容处理需要发生在 LiteLLM 完成格式转换之后，并限定目标上游和 custom 工具；其他原生工具类型需要保留。

此前只验证 extension 注册与沙箱文件能力，模型替身没有覆盖真实提供端的工具协议。这一验证缺口使多次提示词修改未能解决实际问题。

修复后的完成条件是：真实 Pi 对话发起 `write_file` → 后端验证并写入 OpenSandbox → 读回文件 → 发布 `artifact.created` → 聊天卡片和产物面板均可下载。仅 API 200、普通文本“已写入”或工具清单存在均不满足该条件。本次已定位根因，尚未完成此修复后的完整链路验证。
