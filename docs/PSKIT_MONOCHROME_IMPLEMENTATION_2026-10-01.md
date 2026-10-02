# PSKit 黑白工作区实现记录（2026-10-01）

## 已实现

- 通用新聊天作为登录后首页；首次发送时才创建个人会话。项目会话保留原有深链接。
- 深色与浅色灰阶主题，保存到浏览器；侧栏对话选中行为为整行灰色背景，移动端使用抽屉。
- 项目创建、项目详情、个人对话移入项目；消息与运行 ID 保持在原会话上。
- 全局 Skill 与项目 Skill 在对话选择器分组；项目可指定最多 3 个默认 Skill，发送项目消息时由服务端加入默认 Skill。
- 工具集目录与专用页面：PDB 关键词检索接实际 MCP 接口；INABe 与 AlphaFold 3 提供专属输入和状态布局，缺少模型适配时阻止提交。
- MCP 工具调用自动记录为个人运行；“我的运行”可以把结果保存到个人项目，项目详情可查看。Mock 模式为进程内数据，Pi 模式保存在 SQLite。
- 设置页、Skill 目录、登录页灰阶样式。

## 当前能力边界

1. 现有 `search_pdb` Mock 工具返回示例数据。界面提供原始 PDB 记录链接，并明确提示核验数据源；接入真实检索服务后才能当作科研数据使用。
2. INABe 预测接口及 PDB 文件输入协议尚不存在，页面禁用提交与上传。
3. AlphaFold 3 后端现有接口只接受配额、任务和运行 ID，不接受分子实体与序列，因此页面禁用真实预测提交。
4. PDB 实验方法筛选尚无服务端参数，界面保留禁用控件并说明原因。
5. 文件库仍是个人级别，尚无项目文件归档接口。项目页目前聚合对话、默认 Skill 和已保存的工具结果。

## 关键接口

- `PATCH /api/v1/c/{id}/project` 或 `PATCH /api/v1/g/{projectKey}/c/{id}/project`：移动会话，并按地址校验原会话与个人或项目空间的归属。
- `GET/PUT /api/v1/g/{projectKey}/skills`：项目 Skill 列表和默认列表，默认最多 3 个。
- `GET /api/v1/tool-runs`：当前用户的 MCP 工具运行记录。
- `PATCH /api/v1/tool-runs/{id}/project`：将个人工具运行保存到拥有的项目。
- `POST /api/v1/mcp/tools/{name}/invoke`：成功调用后返回 `run_id` 并写入工具运行记录。

## 验证

- `new_frontend`: `npm test` 通过 51 个测试；`npm run lint`、`npm run build` 通过。
- `new_backend`: `python -m pytest -q` 通过 112 个测试。一次完整运行中 AlphaFold 3 的异步测试出现时序失败；单独复现通过，随后完整运行通过。
- 本地真实浏览器连接新版前后端，走通登录、创建项目、PDB 检索、个人运行保存到项目；无页面脚本异常。
- 在 1440×900 检查深浅主题、选中对话、项目弹窗、PDB 结果、已保存运行；390×844 下对 `/tools`、`/tools/runs`、`/tools/pdb`、`/tools/inabe`、`/tools/alphafold3`、`/projects`、`/skills`、`/settings` 逐页检查，`scrollWidth` 均为 390px。

实际页面预览位于 [`design/pskit-monochrome/previews/`](../design/pskit-monochrome/previews/)：深色新对话、浅色工具集、PDB 检索结果、移动端新对话。

设计来源：[Figma 设计稿](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt) 与 [设计规格](./PSKIT_MONOCHROME_UI_SPEC_2026-10-01.md)。
