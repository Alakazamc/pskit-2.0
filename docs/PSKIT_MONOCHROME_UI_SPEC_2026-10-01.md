# PSKit 黑白界面设计交付（2026-10-01）

## Figma 设计稿

[打开 PSKit Monochrome Lab Workspace v2](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt)

设计源稿为 [`design/pskit-monochrome/`](../design/pskit-monochrome/) 中的 HTML、CSS、JS。Figma 中按功能和明暗主题分组；每组内的画面有英文 screen key 标签。已导入 16 种页面或状态、两套主题，共 32 个画面。另有一个单页对话首页预览。工具组的最终版本见标有“最终稿”的一行：首次导入的工具组保留为早期稿，INABe 图表曾存在宽度问题。

| 页面组 | 深色 | 浅色 | 画面 |
| --- | --- | --- | --- |
| 对话 | [深色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=2-2) | [浅色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=3-2) | 新对话、当前对话、Skill 选择器 |
| 项目 | [深色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=4-2) | [浅色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=5-2) | 项目列表、项目详情、默认 Skill 设置弹窗 |
| 工具集最终稿 | [深色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=10-2) | [浅色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=11-2) | 工具目录、我的运行、PDB、INABe、AlphaFold 3 |
| 系统与移动端 | [深色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=8-2) | [浅色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=9-2) | Agent Skills、设置、移动对话、移动侧栏 |
| 组件状态 | [深色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=13-2) | [浅色](https://www.figma.com/design/OmswpqVH2JT0ZMrqMXM6Dt?node-id=12-2) | 对话行默认、悬停、选中、焦点；按钮、Skill chip、表单、任务状态 |

`generate_figma_design` 以可编辑的原始 frame/text/vector 层导入页面，不是 Figma 组件实例库。当前 Starter 账户的 Figma MCP 编辑/检查额度已用尽，因此还不能建立 Figma Variables、Variants、Code Connect，也不能从 Figma 端回读图层结构或截图。此表的完成性依据是每组导入工具的 `completed` 回执和本地浏览器截图；Figma 端逐节点检查仍待额度恢复或席位升级。

## 产品骨架

1. 默认进入通用新对话。侧栏首屏顺序为新聊天、搜索对话、工具集、Agent Skills、项目、最近对话、个人入口。
2. 通用对话默认位于个人空间，右上角“移入项目”可在后续归档。项目目前是个人空间，不设计成员或共享权限。
3. 项目聚合对话、文件、Skill。项目可设置少量默认 Skill（界面建议最多 3 个），新建项目对话时自动启用；其他项目 Skill 与全局 Skill 在对话中明确选择。
4. 工具集从“全部工具”进入 PDB、INABe、AlphaFold 3 的专用工作区。独立运行默认归个人；结果页可保存到项目。“我的运行”收口所有任务状态。
5. 界面仅使用黑白灰层级。结构图、科学图表允许按数据意义采用必要色彩，但必须提供图例、数值和不依赖颜色的说明。

## 视觉与组件规则

| Token / 状态 | 浅色 | 深色 | 用途 |
| --- | --- | --- | --- |
| `bg` | `#FFFFFF` | `#212121` | 主工作区 |
| `side` | `#F9F9F9` | `#171717` | 侧栏 |
| `surface` | `#FFFFFF` | `#262626` | 卡片、弹层 |
| `subtle` | `#F4F4F4` | `#2C2C2C` | 消息、轻量背景 |
| `selected` | `#ECECEC` | `#303030` | 当前导航项和对话整行灰色蒙层 |
| `hover` | `#F2F2F2` | `#292929` | 悬停；不能覆盖当前选中状态 |
| `line` | `#E6E6E6` | `#373737` | 分隔线与边框 |
| `text` | `#171717` | `#F5F5F5` | 主文字 |
| `muted` | `#676767` | `#B7B7B7` | 次要文字 |
| `soft` | `#969696` | `#888888` | 辅助说明 |

- 桌面画板 1440 × 900，侧栏 260px；移动画板 390 × 844，抽屉宽 300px。
- 使用当前前端已设定的 `Inter, Noto Sans SC, PingFang SC, Microsoft YaHei` 字体栈。页面标题约 30px，顶栏标题 17px，正文 12–14px。
- 侧栏导航高 36px、圆角 9px；会话行高 34px、圆角 9px。选中会话整行填充 `selected`，文本切为主色，更多操作保持可见。长标题单行省略，悬停/聚焦时显示完整标题的 Tooltip。
- 键盘焦点需有独立可见轮廓，不能只依赖灰色 hover。导航普通链接使用 `aria-current="page"`；若采用树形项目导航，则用 `role="treeitem"` 与 `aria-selected`。
- 项目默认 Skill 用常驻 chip 表示；临时 Skill 通过选择器加入并可删除。通过菜单选定的 Skill 是结构化引用，不能仅凭用户手打 `/名称` 识别。
- 工具状态统一为未运行、校验错误、排队中、运行中、已完成、失败；页面可保留输入并查看日志、重新运行。结果和运行记录保留工具专属字段。

## 三类工具的专属任务界面

| 工具 | 输入 | 执行反馈 | 结果 |
| --- | --- | --- | --- |
| PDB 结构检索 | 蛋白名称、UniProt/PDB ID、物种、实验方法 | 检索数和筛选条件 | 列表含方法与分辨率；结构预览和原始记录入口 |
| INABe 结合位点预测 | 序列或 PDB 文件、预测模式 | 格式校验与任务状态 | 位点概率图、区间表、数值图例、下载与保存到项目 |
| AlphaFold 3 | 多分子实体、序列、随机种子 | 排队与阶段进度，可离开页面 | 3D 结构、pLDDT/PAE、界面评分和文件下载 |

## 后续实现时优先复用

本轮交付范围是 Figma 设计，不修改 `new_frontend`。现有依赖已包含 Radix、Lucide、react-dropzone、react-resizable-panels、TanStack Query、Zustand 和 `@assistant-ui/react`。但 `@assistant-ui/react`、Radix Dialog/Popover/Tooltip/Slot 在当前产品源码中尚未实际导入；不要把“依赖已安装”等同于“页面已使用成熟组件”。实现时可优先用 Radix Dialog 做项目 Skill 弹窗、Popover 做 Skill 选择器、Dropdown Menu 做会话操作、Tooltip 做截断说明，Lucide 用于图标；输入区与工具表单保持可复用但不要强行让不同模型共用同一结果组件。细节出处见[参考源码核查](./UI_REFERENCE_RESEARCH_2026-10-01.md)。

## 验证记录

- `node --check design/pskit-monochrome/app.js` 通过。
- 本地 Chromium 实际检查了深色新对话、深色当前对话、浅色工具集、深色 INABe、浅色 AlphaFold 3、深色移动侧栏。发现并修复 INABe 图表的宽度和深色高概率柱条对比问题后，重新导入工具组。
- 浏览器批量检查 32 个画面均为预期的桌面或移动尺寸，未出现页面脚本异常；另外确认 INABe 深浅两套图表各有 62 根概率柱。首次批量检查把坐标轴的 5 个标签也计入 `span` 数量，随后改用图表专用选择器核实。
- 每个功能组两套主题均由 Figma capture 返回“design has been added”。不能声明已在 Figma 端逐节点验证、创建组件实例或绑定变量，因为 Starter MCP 额度阻止了 `get_metadata` / `use_figma`。
