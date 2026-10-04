# 聊天框模型与推理强度选择器设计

日期：2026-10-04

状态：待书面规格审阅

## 目标与现状

用户在同一个聊天框里，从 LiteLLM 授权的多个模型中搜索、选择模型，并为支持推理控制的模型选择实际生效的推理强度。界面沿用新版 PSKit 的薄荷绿主题和中英双语。借鉴 ChatGPT 网页版将选择器放在输入框内、在同一入口调整速度与推理投入的交互，不复制其视觉样式或产品分级。参考：[ChatGPT 发布说明](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)。

现有 `Composer` 使用原生 `<select>` 展示 LiteLLM 返回的原始别名；线上可见二十多个 Anthropic 别名，含不应直接展示的 `anthropic/*` 通配项。`ModelOption` 只包含 `id` 与 `supports_images`；消息请求只保存 `model`，Pi 的 `models.json` 没有推理能力标记，也没有在提示前发送推理等级。当前 `new_backend/pi/package.json` 固定 `@earendil-works/pi-coding-agent` 0.87.1；该版本的 RPC 支持 `get_available_thinking_levels` 与 `set_thinking_level`。参考：[Pi RPC 命令文档](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc-commands.md)。

## 交互设计

输入框工具栏左侧放一个紧凑触发器，显示当前模型的短名称和强度，例如 `Claude Opus 4.8 · 高 ▾`。点击后打开位于输入框上方的可搜索弹层：顶部为搜索框与当前选项，已选模型置顶，其余列表按服务商分组、完整别名作为次要文字。选中模型后，弹层下部显示该模型可用的推理强度单选项。模型不支持推理控制时，隐藏强度区并显示简短说明。移动端弹层限制在视口内，模型列表可滚动，按钮具备键盘焦点与足够的触摸区域。

强度选项由后端的逐模型能力数据决定。首项 `默认` 不传强度，保留现有模型调用行为。其余选项以用户能理解的 `关闭 / 极低 / 低 / 中 / 高 / 极高 / 最大` 标签显示，内部值分别为 Pi 的 `off / minimal / low / medium / high / xhigh / max`；其中 `off`、`minimal`、`low`、`xhigh` 与 `max` 仅当 LiteLLM 明确标记支持时提供。文案提示更高强度可能使用更多 Token，不承诺固定耗时或质量。切换模型后，若旧强度不被新模型支持，恢复为 `默认` 并在选择器中体现。已附加图片时，不支持图片的模型显示原因并不可选；上传时仍由现有前后端校验兜底。

模型 ID、能力和可用性始终来自服务端，不在组件内写死模型名单。弹层可按模型名和服务商搜索；保留完整别名，避免相似版本的展示名造成误选。模型列表为空、请求失败、已选模型下线时显示明确状态。发送成功后保留模型和强度选择，清空消息正文与附件；旧消息和正在运行的 Run 不受之后的选择改变。

## API 与能力来源

`GET /api/v1/models` 继续从当前用户获授权的 LiteLLM 虚拟 key 查询别名。过滤空值、通配符别名和明确不可用于聊天的条目；若网关临时不可达，沿用当前有界缓存/默认模型策略。扩展公开 `ModelOption` 为 `id`、可选显示名、`supports_images` 与 `reasoning_levels`。后端从 `/model/info` 读取 `supports_reasoning`、`supports_none_reasoning_effort`、`supports_minimal_reasoning_effort`、`supports_low_reasoning_effort`、`supports_xhigh_reasoning_effort`、`supports_max_reasoning_effort` 等标记，保守生成允许列表：只有 `supports_reasoning=true` 时才给出 `medium/high`，额外等级只在对应标记为 `true` 时给出。元数据缺失或矛盾时不推测该能力。`默认` 由前端提供，不是一个发送给 Pi 的等级。

`MessageRequest` 增加可选 `reasoning_effort`，值限定为 Pi 固定版本的等级枚举。后端先按 `model` 解析当前有效别名，再校验所请求等级属于该模型公布的 `reasoning_levels`；不支持时返回稳定的 422 错误码，不创建 Run 或扣除配额。幂等指纹包括模型与强度。请求、OpenAPI 生成的 TypeScript 类型和 mock API 契约同步更新；前端不得自行请求 LiteLLM 或持有网关密钥。

## Pi 执行与持久化

把本次模型与强度存入 `Run` 的现有上下文；不为 UI 偏好新增业务数据库表。`AgentService` 从 Run 上下文构造内部环境变量，并在重试、长任务完成后的自动恢复时继续读取同一值。没有显式强度时，Pi 的模型配置和 RPC 调用维持现在的行为。显式指定时，Pi 的临时 `models.json` 给当前模型标记 `reasoning=true`；在 `prompt` 之前通过 RPC 查询可用等级、设置请求等级并核对响应。RPC 拒绝时将 Run 标记为失败并返回可识别错误，不能默默回退到其他强度。SandboxPiRunner 通过现有私有请求与环境传递此值，沙箱桥接器沿用现有所有者校验。界面只展示用户选择的模型和等级，不展示隐藏推理内容。

## 状态与兼容

模型与强度是聊天输入的 UI 状态，服务端项目/消息列表仍由 TanStack Query 管理。选择可按会话保存在浏览器本地；切换会话恢复各自草稿，刷新后再次与最新模型列表核对。旧客户端未传 `reasoning_effort` 时保持现有请求语义；旧会话、既有 Run、持久化 Pi transcript 不需要迁移。新 UI 面向中文和英文使用同一 API 契约。模型下线或强度元数据变化后，前端提示并重置失效选择；后端最终校验不依赖前端缓存。

## 验收边界

- Mock API 下，多模型搜索、分组、选择、移动端与键盘操作、失效模型和图片兼容状态均可操作；不显示通配符条目。
- 发送请求携带准确的模型 ID 和可选强度；`默认` 省略强度；不支持的模型/等级在创建 Run 前被拒绝。
- Pi RPC 在提示前设置显式等级；重试与异步恢复沿用 Run 的等级；未显式选择时与现有运行行为一致。
- 中英文文案完整，选择器在窄屏不溢出；新界面与旧会话数据兼容。
- 在 Staging 使用至少两个实际 LiteLLM 模型验证列表和一次低成本推理等级调用后，再发布到生产。生产额度仍由 PSKit Token 配额与 LiteLLM 预算控制。
