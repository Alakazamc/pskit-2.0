# 用户 Skill 与工作空间入口设计

日期：2026-10-05。状态：**设计稿，尚未实施，待书面审阅**。

## 1. 目标与范围

用户要把 PSKit 的科研能力变成易发现、易选择、可查看和可调整的 Skill，让用户围绕研究对话工作。此前沙箱、通用计算与管理台的交付已完成并提交；本轮交付产品交互设计、源码能力盘点和追踪方案，不修改运行中的产品。

ChatGPT 的官方项目说明以同一项目内的聊天、文件和指令作为长期工作空间，并将文件预览与工具选择放回工作上下文；本设计借鉴这种组织方式。具体卡片尺寸、目录编辑、会话 Skill 复选框和工作空间开关是按用户要求设计的 PSKit 交互，不声称 ChatGPT 已提供同样功能。[官方项目说明](https://help.openai.com/en/articles/10169521-projects-in-chatgpt)

明确要求：

- 后续 UI 沿用 ChatGPT 网页式的内容层级与交互习惯：中性底色、简洁列表、渐进展开、小面板、少导航；延续已确认的模型 / 推理强度入口。
- Skill 目录有搜索，卡片约 100 × 80 px；点击后在小面板中查看目录与文件，上方斜铅笔进入编辑。
- 产物入口归属于规划的右上角，不占侧栏的独立导航项。
- 盘点并迁入旧科研能力；设置选择默认 Skills，对话输入框左下角勾选本会话 Skills。
- 选择结果在用户容器中形成会话级注册文件；容器启停由用户可控。
- 设置居中、简洁；继续支持中英双语、浅色 / 深色和移动端。

**待澄清的产品名：**“Lite ML”可能是 LiteLLM 或 MLflow，已提出澄清。当前推荐将注册与授权保留在 PSKit，观测通过 adapter 接入；UI 和数据契约不依赖澄清结果。研究结论及当前版本限制见[官方研究](../../research/2026-10-05-skill-registry-tracing-research.md)。

## 2. 现状与真实缺口

| 现状 | 需要补齐 |
| --- | --- |
| `MonoWorkspace.tsx` 的 `/skills` 是大卡片网格，只展示名称、简介、版本 | 搜索、紧凑卡片、详情文件目录、编辑、会话选择 |
| `CatalogItem` 只有 ID / name / description / version | 包结构、digest、可用性、发布状态与文本文件读取 |
| 新版源码只有 `structure-review` 一份文件型 Skill | 逐项迁移旧能力；不把设计目录误报成服务已接通 |
| `composerStore.clearAfterSend()` 清除 Skills，全局草稿 store 未按会话分区 | 会话选择持久化，发送只清正文和附件，切换会话不串选项 |
| 项目默认 Skills 每轮被后端合并，客户端传空数组也不能取消 | 改为新会话初始化快照；显式空选择必须有效 |
| 项目默认 Skill 数量当前限制 3 | 改为可配置注册与上下文预算，不能继续硬编码 3 个 |
| Pi 当前 `--no-skills` / `--no-builtin-tools`，全文通过 Python system suffix 注入 | 受控包物化、按需加载；不是创建目录就会自动生效 |
| 用户沙箱已有后端管理 API，但普通用户没有开关 | 用户所有权控制、启停意图、真实状态与保卷暂停 |
| 产物在侧栏 `/artifacts`，实际聊天规划在 `Conversation.tsx` | 把入口放入实际聊天规划；不只改当前未使用的 `AgentPanel` |
| 设置是几个宽面板纵向堆叠 | 居中的统一设置容器，左分类、右侧简短设置行 |

## 3. 方案比较与推荐

| 方案 | 体验与成本 | 判断 |
| --- | --- | --- |
| **A：用户目录 + 默认选择 + 会话注册，PSKit 主注册表，观测外接** | UI 可独立基于 API 契约开发；复用现有权限、Run、容器；需要补包管理和会话选择 | **推荐**，与现有 Python / Pi 架构衔接直接 |
| B：把完整 Skill 生命周期交给 LiteLLM Managed Skills / harness | 能复用部分目录治理，但官方分发支持表未列 Pi，仍需适配完整包、授权与固定版本 | 可作为未来目录来源或导出目标；本轮不作为运行前提 |
| C：MLflow 同时承担 Skill 包注册、容器加载和 UI | trace / eval 很适合；完整包权限与 Pi 注册仍需自建，增加运行依赖 | 用于追踪 / 评估，不取代现有业务注册表 |

LiteLLM 已有 Managed Skills，不能说它完全不支持 Skill 管理；实际云端部署的版本和授权是否覆盖这些新能力仍须在接入时核对。上述判断是设计建议。[LiteLLM Marketplace](https://docs.litellm.ai/docs/tutorials/claude_code_plugin_marketplace)、[Harness Skills](https://docs.litellm.ai/docs/harness/tools)、[MLflow Prompt Registry](https://mlflow.org/docs/latest/genai/prompt-registry/create-and-edit-prompts/)

## 4. 信息架构与路由

用户侧常用导航：新对话、Skills、工具集、项目、最近对话。设置位于账号入口。管理台继续使用已有独立 `/admin/*` 区域，普通用户不需要看到模型服务发布 / 容器运维等操作。

- `/skills`：目录，query 保留搜索和分类状态。
- `/skills/:skillId`：直接打开对应详情面板；关闭返回原列表并恢复滚动 / 搜索。
- `/skills/:skillId?version=:version&file=:relativePath`：可定位版本和文件；路径由服务器验证。
- `/session/:sessionId` 与 `/p/:projectId/c/:sessionId`：保留现有规范会话地址。
- 会话的 `?panel=artifacts&run=:runId` 打开该规划 / Run 的产物；关闭不离开对话。
- `/settings?tab=general|skills|workspace|usage|account`：居中的设置容器，支持深链接。
- 移除侧栏 `/artifacts` 入口；旧地址作为兼容入口，已有带 run/session 参数的链接跳转至原会话。无归属参数时，仅在可确定最近有权访问的产物会话时跳转；没有可用目标则显示简短“选择一个对话查看产物”及会话入口，不能跳到别人的 Run 或凭空选任务。

工具集保留直接任务操作，不新增重复的“Skill 执行器”页面；工具页需要模型时继续走现有模型策略与 Run 接口。

## 5. Skills 页面

### 5.1 目录

内容宽度桌面 740 px，标题只有 “Skills”。右侧次级入口“默认启用”。下面是 44 px 高的搜索行，搜索名称、短描述、ID 和标签；输入框本身无白框，键盘焦点通过容器背景显现。

分类使用紧凑横向文字按钮：全部、默认启用、检索、结构、预测、设计、报告。无需大图、封面、长介绍或展示所有工具参数。

卡片默认 **100 × 80 px**，间隔 10 px，圆角 9 px；名称最多两行，短描述一行，字号不低于 12 px。完整名字和描述可在可访问名称及详情中查看。宽屏不拉成大块；窄屏约 100–114 px 随列分配，200% 文字缩放允许高度增长，不裁掉功能。

目录区保留搜索和过滤条件；搜索 150–250 ms 防抖，查询在服务器执行并支持游标分页，每批最多 48 条。超过 96 个已加载项使用按行虚拟化，不能把数千个 DOM 卡片和全文一次送到客户端。排序与游标稳定，选择状态按 ID 独立保存。

只有已授权且已发布的条目进入普通目录。未接通依赖的拟开放 Skill 可在明确的“待接入”过滤中查看说明；不能勾选成可用，也不计入已启用数量。详情展示具体缺失项；该状态不是假数据填充卡片。

### 5.2 空状态与错误

- 搜索无结果：保留搜索框，提示没有匹配项，提供清除过滤。
- 无可用 Skills：解释当前账号尚无可用技能，不显示模拟条目。
- 网络失败：同一位置重试，保留查询和已选 IDs。
- 某技能权限撤销 / 服务下线：显示状态变化；Run 准入前服务端复核，不为已知不可用选择创建收费执行。准入后的权限撤销继续阻止实际调用，已经发生的真实消耗按现有规则记账。

## 6. Skill 详情与编辑

桌面使用居中 **720 × 480 px** 面板：上方名称与一句简介，右上角斜铅笔及关闭；左侧 174 px 文件目录，右侧显示当前文件。移动端面板适配屏幕，目录折叠为顶部可展开列表。

示例包结构（迁移时按实际内容，不伪造不存在的文件）：

```text
skill-name/
├── SKILL.md
├── manifest.json
├── references/
│   ├── inputs.md
│   └── evidence.md
├── scripts/       # 有实际脚本才展示
└── assets/        # 有实际素材才展示
```

文件正文按路径惰性获取，默认选择 `SKILL.md`；Markdown 阅读模式与纯文本 / 代码模式独立。目录只列包内相对路径；二进制显示类型和下载，不强行解码成文本。

编辑规则：

1. 公共 Skill 的铅笔创建**个人副本的草稿**；不能直接覆盖所有用户使用的公共版本。已有个人 Skill 编辑产生新版本。
2. 进入编辑后提供取消和保存；保存时说明个人副本，退出有未保存修改时可保留草稿 / 放弃。
3. 文件编辑与包发布分开。普通用户不能修改公共发布状态、扩展工具权限或替换其他用户的包。`manifest` 的 ID / version / permissions 等受保护字段通过结构化规则生成，不能作为任意文本写入权限来源。
4. `scripts` 可查看；文本草稿的编辑不赋予执行权限。发布的脚本执行仍需平台已有受控能力或另行受控执行器，不能因在容器中有文件就开启任意 shell / Python。
5. 保存校验包结构、长度、路径、内容类型、有效依赖和 expected_revision。并发冲突保留草稿并提示版本变化；不静默覆盖。
6. 保存新版本不修改正在执行的 Run；个人副本可显式替换当前会话选择，下轮生效。过去的对话和后台唤醒仍使用其固定版本。

详情底部是默认启用开关 / 版本简讯，避免编辑、发布、运行三个主按钮争夺注意力。发布公共 Skill 属于管理员流程；后续可在管理台增加相应发布页，用户详情不承担管理后台职责。

## 7. 默认选择与会话选择

### 7.1 默认选择

设置 → Skills：搜索 + 勾选列表，已选置顶，显示数量和保存。保存后作用于**新建会话**，不改写已有会话选择，不启动 Docker。项目可保留自己的默认集合：项目会话优先使用项目已设置的默认集合；项目未设置时使用用户默认集合。

新建会话时存一份 `SessionSkillSelection`，记录来源（user_default / project_default）、Skill ID、固定发布版本及 digest。**未初始化和显式选择空集合是两种状态。** 初始化完成后，不再每轮合并项目默认项；用户取消默认 Skill 后必须保持取消。

已发布版本升级不会自动改变旧会话，可在列表中给出“有新版”的次级提示，用户主动更新后只影响后续 Run。当前权限、服务发布状态和配额仍在每次使用时复核，固定旧版本不能绕过撤销。

### 7.2 对话框入口

左下角 `+` 后增加 `Skills N ▾` 入口；右下角模型 / 推理强度 / 发送与停止保持已有布局。点击向上打开约 **338 px 宽、最高 380 px** 的小面板：搜索、已选置顶、名称 + 一行用途、复选框；底部“恢复默认”。

- 勾选表示本会话允许助手使用此 Skill，不等于已经触发。不同会话保存独立集合。
- 持久化成功才成为已保存选择；可乐观显示，但失败时回滚并保留重试入口。
- 运行中可调整“下一轮选择”，明确提示下一轮生效，不更改当前 Run 快照，也不偷偷终止运行。
- 成功发送只清正文 / 附件 / 当轮临时资源；不清本会话 Skills。
- 切换会话按 session_id 加载选择与草稿，不能沿用另一会话的全局 Zustand Skills。
- `/` 保留为快捷查找 / 显式调用入口，复用同一目录。显式调用的选择方式被记录；不把 `/foo` 原样当作已加载证明。
- 当已选项被撤销或依赖不可用时保留可解释的选择状态，不静默用其他 Skill 替代；恢复默认仅恢复当前有权访问的项。

初始默认推荐沿用当前可用 `structure-review`；新迁移能力通过验收后再由用户选择。GPU Skill 不默认开启，游客的 GPU / AF3 限制继续生效。

## 8. 注册、上下文与 Pi 的衔接

### 8.1 唯一记录与物化文件

Python 的注册表 / PostgreSQL 保存权威版本、授权、选择和 Run 快照。容器中的注册文件是物化结果，不接受浏览器或模型直接修改后扩权。

每用户一个容器及持久卷；每会话单独目录：

```text
/workspace/
├── skill-cache/<skill-id>/<version>-<digest>/   # 校验后的包缓存
└── sessions/<session-id>/
    ├── .pskit/skills/active.json                # 本会话已保存选择
    ├── .pskit/skills/runs/<run-id>.json          # 该 Run 不可变快照
    ├── files/
    └── outputs/
```

`active.json` 建议字段：schema_version、selection_revision、source、每项的 id/version/digest/package_ref。物化时使用 staging 目录、路径与哈希校验、原子替换；禁止 symlink 穿越、包解压跳出根目录、用客户端传入宿主机路径。容器暂停时只保存 DB 选择，下一次启动 / Run 准入再同步。并发更新用 expected_revision；同会话准入与物化串行。

活动 Run 使用其快照；后台完成唤醒沿用原 Run 选择，而不是后来修改的默认集合。若用户主动暂停，则结果仍登记，唤醒保持待恢复状态；用户恢复后再执行，不能无视其暂停意图自动开机。

### 8.2 数千 Skills 的上下文预算

“注册”表示技能可被检索、加载和授权执行，不表示把每个 SKILL.md 全文永久拼进提示词。

1. 已选全集保存在会话注册文件及后端索引，未选技能不进入本会话可调用集合。
2. 小集合可向 Pi 提供预算内的名称 / 简述索引；大集合只提供集合概况和受控 `search_skills`，按请求返回有限摘要。不能默默丢弃用户选择。
3. 显式选择执行或助手匹配后，`load_skill` 返回经过授权的指定版本正文 / references，记录 digest；一次加载结果幂等去重。
4. Skills 依赖的能力调用继续通过已发布服务和服务器授权；平台可提供稳定的受控能力入口，在服务器验证具体 capability ID 和输入 schema，避免一次注册数千个工具 schema。
5. 注册数量、包体积、摘要和全文上下文预算可配置。超过预算明确反馈；不沿用最多 3 个的旧限制，也不将默认选择强制解释为无限 Token 消耗。

当前 Pi 默认资源发现关闭，且无通用 read/bash；第一版扩展应使用受控 loader / 既有注册工具接口，不靠打开用户主目录自动发现实现。若采用 Pi 显式 Skill 目录，必须只暴露已授权的版本并补齐受限读取路径；不得打开任意 shell 来获得文件读取。Pi 当前的禁用选项和 `allowed-tools` 不是平台 ACL。[固定版本 Skills](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/docs/skills.md)、[资源加载源码](https://github.com/earendil-works/pi/blob/v0.87.1/packages/coding-agent/src/core/resource-loader.ts)

关闭 Skill 只禁止后续准入与调用；已读入的历史文字可能仍存在于 transcript。需要完全去掉历史指令时，用户新建对话；不宣称修改注册文件会消除旧上下文。安全撤销在实际服务端执行点立即生效。

## 9. 规划与产物

实际 `Conversation` 的规划标题右上角提供文件夹图标 + `产物 N`。点击展开小列表，点击具体文件进入当前会话的预览面板；保留预览和下载能力。该入口不是单独导航页或大面积常驻结果栏。

- 当前 Run 的产物与已保存产物按 ID 合并，不按文件名去重；关联 plan_step_id 时在文件下方显示步骤。
- 未建立 plan_step_id 的旧产物显示“本次研究”，不能猜它来自哪一步。
- Run 有产物但没有规划时显示同样的“本次研究”紧凑标题和产物入口；不为了显示文件制造计划步骤。
- 新产物通过同一事件流更新数量；刷新从 owned snapshot 恢复。预览 / 下载均服务端校验归属。
- 在历史回答 / 工具结果中保留产物引用，仍可访问相应历史 Run；切换会话立刻隔离旧面板内容。
- 文件尚未提交完毕则显示处理状态；不得在可下载前给出假链接。

## 10. 设置与用户容器控制

### 10.1 居中的设置

桌面 780 px 最大宽的统一容器，顶部“设置”与关闭按钮，左侧 158 px 分类，右侧简单设置行。页面水平居中，短屏保留外层滚动；避免一张占满屏幕的大卡片加另一张大卡片。

分类：常规（外观 / 语言）、Skills（默认选择）、工作空间（运行开关 / 状态）、用量（Token / GPU / 文件）、账号。只保留帮助用户选择的简短说明；不在用户用量页展示 LiteLLM / new_api 品牌和模型网关实现细节。

移动端分类变成顶部横向选项；设置行和列表单列。读写反馈、禁用原因、键盘 / 屏幕阅读器和浅深主题都采用共享 tokens 与滚动条。

### 10.2 启停语义

容器控制显示为“工作空间”，不要求用户理解 Docker。设置页提供“运行工作空间”开关，后台保存 desired_state（running / stopped），UI 独立显示 observed_state（starting / running / pausing / stopped / unknown / failed）。

- 启动：只允许当前用户自己的工作空间，恢复持久卷，应用最新会话注册；启动成功才显示运行中。
- 暂停：立即禁止该用户的新 Pi Run 和新准入。无活动 lease 时停止；有活动任务提供“完成后暂停”和“取消后暂停”，取消尚未确认不能显示停止。
- 暂停不会删除卷、会话、文件或已经完成的结果。删除数据是另一个有明确确认的动作，不复用运行开关。
- 用户明确暂停后，聊天提交应说明需先恢复，允许该条用户操作恢复后再发送；后台唤醒 / 文件同步不能擅自调用 ensure 重启。
- 空闲回收保持现有节约资源机制（目前默认 30 分钟），只回收无活动资源；用户 desired_state=running 时下一次主动操作可恢复。它与用户主动停机是不同状态来源。
- A6000 的远端计算 Job 不在用户容器里：暂停用户容器不代表 AF3 已停，也不自动销毁远端任务。暂停面板说明仍在运行的数量与恢复后可继续分析；需要取消时使用现有远端取消流程。
- 一个用户多个会话共享容器，开关影响该用户所有会话；面板提示范围，禁止对话开关看起来只停当前 session。

管理员可因维护或配额限制暂停；用户不能覆盖管理员 drain / unknown 状态。管理 API 的管理员能力不能直接暴露给浏览器复用。

## 11. API 与领域对象（待实现契约）

所有接口沿用 Python `/api/v1`，UI 只经 API client，使用真正的 HTTP mock 契约开发；示例数据不写入正式组件。

| 接口 | 目的 |
| --- | --- |
| `GET /skills?q=&category=&cursor=&limit=` | 授权目录分页；摘要不含全文 |
| `GET /skills/{id}/versions/{version}` | 版本、digest、来源、实际文件树、可用性 |
| `GET /skills/{id}/versions/{version}/files?path=` | 校验相对路径后读取有权访问的文件 |
| `POST /skills/{id}/drafts` | 创建本人个人副本草稿 |
| `PUT /skill-drafts/{draftId}/files?path=` | 保存文本，expected_revision 防覆盖 |
| `POST /skill-drafts/{draftId}/versions` | 校验后创建个人不可变版本；发布公共包另走管理权限 |
| `GET/PUT /me/skill-preferences` | 用户默认集合，区分未设置与空集合 |
| `GET/PUT /c/{sessionId}/skill-selection` | 当前会话已保存集合及 revision；对项目路径保留一致 scope 检查 |
| `POST /c/{sessionId}/skill-selection/reset` | 显式恢复当前有权访问的默认集合 |
| `GET /runs/{runId}/artifacts` | 该 Run 的 owned 产物与步骤关系 |
| `GET /me/workspace` | 本人 desired / observed 状态、活动数量、受限原因 |
| `POST /me/workspace/start` | 幂等启动本人工作空间 |
| `POST /me/workspace/pause` | expected_revision + after_finish / cancel_confirmed，真实操作状态 |

重要对象：`SkillPackageVersion`、`SkillFileEntry`、`SkillDraft`、`UserSkillPreferences`、`SessionSkillSelection`、`RunSkillSnapshot`、`SkillInvocation`、`WorkspacePreference`。优先扩展现有 Catalog / Sandboxes，避免新增重复真相来源。

`MessageRequest` 推荐携带 `skill_selection_revision` 和可选当轮显式调用 refs；服务器在 Run 准入固定实际版本。保留旧 `skills` 引用契约时必须定义 legacy 兼容方式，不能把客户端名称或文件内容视为受信任指令。幂等 fingerprint 基于服务端固定的选择快照，重试不得因用户随后修改选择而产生第二个 Run。

所有读取 / 写入校验 ownership、发布状态、当前授权和资源配额。草稿包与观测事件受存储配额 / 保留策略；Skill enabled 是用户偏好，绝不能代替授权 grants。包摘要和文件树不泄露宿主路径或服务密钥。

## 12. 触发记录与优化

先把事实写入现有持久化业务事件 / outbox，不要求再部署一个重型任务系统：

| 事件层次 | 含义 |
| --- | --- |
| `skill.available` | 提供索引 / 摘要，未必被使用 |
| `skill.requested` | 用户或路由请求指定版本 |
| `skill.loaded` | 指定 digest 正文已加载到该 Run；不代表模型遵守了所有规则 |
| `skill.tool_invoked` | 对应真实工具 / Job / attempt |
| `skill.completed/failed` | 实际可核验终态；仅 Run 完成不能推出所有选中 Skill 成功 |

关联 user/session/run/skill/version/digest、调用方式、tool_call/job/attempt、LiteLLM call ID、耗时和产物。助手未采用该 Skill 时不能给已选项生成“成功触发”记录。多个 Skill 关联一次 LLM 调用时保留多关联，Token 总量不复制累加；GPU / CPU 计量仍来自真实计算服务 UsageReport，而非 LLM 日志中的墙钟时间。

LiteLLM adapter 负责模型调用 metadata / tags / callback 关联；MLflow adapter 可补 Run → Skill → Tool / Job / LLM 的 spans 与离线评估。两个 exporter 都可关闭、异步发送和幂等去重；观测服务不可用不阻塞研究。[LiteLLM Logging](https://docs.litellm.ai/docs/proxy/logging)、[MLflow Tracing](https://mlflow.org/docs/latest/genai/tracing/app-instrumentation/manual-tracing/)

优化使用独立样本集，覆盖该触发 / 不该触发、正确工具、证据、结果、恢复、耗时和成本。候选指令经评估 → 审阅 → 新版本 → 显式发布；不自动修改当前用户启用的生产版本。模型 judge / 优化调用有单独预算，不悄悄消耗普通用户额度。第一批只建立可观察记录与确定性评估，是否部署 MLflow 或开启模型优化须另行确定。[官方 Skill 评估](https://mlflow.org/blog/evaluating-improving-agent-skills/)

## 13. 旧能力迁入

完整来源和逐项表见[旧能力盘点](../../research/2026-10-05-legacy-skill-inventory.md)。已确认 28 工具、15 文档名称（含 3 个路由 / 帮助约定）、1 个闭环 Skill 的 11 阶段；这些数量相互重叠。

本设计提出 30 个迁移条目，另保留当前 `structure-review`。每个条目都有来源映射；CORAL / PepCCD 远端别名不重复建卡。迁移的 SKILL.md 必须包含操作方法、触发条件、输入、工具依赖、异步结果处理、产物和证据要求，不能只有一句描述。

顺序按实际依赖：检索 / 结构处理与结果读取 → 模型预测 / 同源分析 → 候选生成 / 排序 / AF3 批量验证 → 闭环与报告。服务尚未发布时目录只能处于待接入，不设置为用户默认可运行。前端可以基于各状态的 HTTP mock 开发；代码验收与真实服务联调分别记录。

## 14. 交付拆分与验收要求

这是产品层总设计，实施应拆成有边界的子计划；本文件不是逐文件执行计划：

1. **目录与设置 UI / HTTP 契约**：紧凑搜索、文件面板、编辑、会话选择、规划产物、居中设置；前后端可在接口固定后并行。
2. **Skill 包、选择、Pi 注册**：不可变包 / 个人草稿、默认与 session 选择、Run 快照、受控物化与 loader；复用当前 Registry / Pi 工具 ACL。
3. **用户工作空间控制**：desired_state、准入拒绝、暂停 / 恢复、活动 lease、远端结果等待与多会话范围。
4. **旧能力批次与触发观测**：按表逐项迁移和服务验收；PSKit 账本先行，LiteLLM/MLflow exporter 独立接入。

用户此前授权 TDD；实施阶段按公共 HTTP、受控 Pi、PostgreSQL / 真容器边界编写失败再通过的验证，外部模型服务才使用替身。

验收覆盖：

- 0 / 1 / 30 / 1000 条目录搜索分页；卡片的 100 × 80 尺寸、中文 / 长英文、空状态与离线失败。
- 详情真实文件树、包路径拒绝、个人编辑不覆盖公共版本、并发冲突、未保存退出、二进制预览。
- 未设置 / 空集合、用户与项目默认、新旧会话、发送后保持选择、切换会话和运行中改下轮选择。
- 数千注册不把全文一次拼入上下文；撤销权限、禁用历史指令、固定版本和后台唤醒。
- 规划无产物 / 产物无规划 / 历史 Run / 多产物事件，预览下载与跨用户拒绝。
- 用户暂停的真实状态、活动 / unknown lease、保卷恢复、禁止后台擅自开机、远端 AF3 仍在运行的说明。
- 观测事件去重、真实 loaded 与 tool 调用事实、Token 多关联不重复计量、exporter 故障不阻塞。
- 1360 / 390 px，浅 / 深主题，中 / 英文，键盘与 200% 缩放；现有流式、停止、附件、模型选择行为不回退。

## 15. 设计稿与审阅

[可点击 HTML 设计稿](../../design/2026-10-05-skills-workspace/index.html) 展示目录、详情、会话选择、规划产物和设置。它使用明确标注的示例内容，全部状态只在浏览器内存变化，不连接 API、不写数据库、不运行容器或模型。正式 UI 必须按 HTTP 契约取得数据。

本轮设计自检关注：用户明确需求已覆盖；不存在“选中即成功触发”“创建目录即 Pi 生效”“关容器即取消 AF3”等混淆；待澄清产品名不会阻塞目录与会话 API。书面设计确认后再编写实施计划并开始产品实现。
