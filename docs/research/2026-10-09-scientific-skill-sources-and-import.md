# 科研 Skill 来源、引入规则与首批目录

日期：2026-10-09  
状态：研究结论，供后续规格与实施计划使用

## 1. 目标

为 PSKit 建立一批可追溯、可授权、可按需加载的科研 Skills，并为用户自定义 Skill 的私有保存与公开发布提供边界。本研究只确定来源、候选清单与准入规则；具体 API、数据表、Composer `@` 交互和发布审核流程在书面规格确认后实施。

## 2. 外部格式与加载原则

PSKit 应兼容 Agent Skills 的目录格式：每个 Skill 是一个目录，必须包含 `SKILL.md`，可包含 `scripts/`、`references/` 和 `assets/`。目录元数据用于发现，完整说明在选中或触发后加载，引用文件继续按需读取。这种渐进加载方式适合数百到数千个 Skill，不需要把所有 Skill 正文永久放进模型上下文。

来源：

- [Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx)
- [Adding skills support to an agent](https://github.com/agentskills/agentskills/blob/main/docs/client-implementation/adding-skills-support.mdx)

PSKit 在该格式之上增加服务端授权和固定版本。`allowed-tools`、Skill 自己的脚本或上传者填写的依赖只作为声明，不能授予新的平台工具权限。Pi 只能通过后端确认过的版本、依赖和用户选择加载 Skill。

## 3. 候选来源

### 3.1 首选可引入来源

1. [Google DeepMind science-skills](https://github.com/google-deepmind/science-skills)
   - 覆盖基因组学、结构生物学、化学信息学和文献研究。
   - 仓库软件采用 Apache-2.0，其他材料采用 CC BY；部分 Skill 仍有第三方数据条款，正式导入前必须逐项检查 `SKILL_LICENSES.md` 并保留归属信息。
   - 适合作为首批包内容的主要上游。
2. [Awesome Genomic Skills](https://github.com/GoekeLab/awesome-genomic-skills)
   - CC0-1.0 的科研 Skill 索引，适合持续发现上游项目。
   - 它是目录而不是每个 Skill 内容的统一许可证；导入时仍须检查实际来源。

### 3.2 只作能力发现的来源

1. [OpenAI life-science-research plugin](https://github.com/openai/plugins/tree/main/plugins/life-science-research)
   - 汇总了遗传、表达、蛋白质、化学、临床、文献与多组学相关能力，适合确定用户最常用的科研入口。
   - 本次研究没有在仓库根目录确认到可覆盖全部 Skill 文本的许可证，因此不能直接复制其内容；只能用作目录发现和自行实现时的功能参考。
2. [Anthropic life-sciences](https://github.com/anthropics/life-sciences)
   - 包含 PubMed、ChEMBL、Open Targets、单细胞 RNA 质控、Nextflow 和 scvi-tools 等科研集成。
   - 其中相当一部分是 MCP 或外部连接器，不能作为纯文本 Skill 直接导入；需要先接通对应能力并确认单项许可。
3. [ClawBio](https://github.com/ClawBio/ClawBio) 与 [SciAgent-Skills](https://github.com/jaechang-hits/SciAgent-Skills)
   - 纳入后续候选池；正式引入前继续核对许可证、维护状态、依赖和输入输出契约。

## 4. 首批目录建议

首批优先收录公共数据库检索与分析编排能力。这些能力使用频率高、输入输出容易结构化，也能复用 PSKit 已有 MCP、通用计算和产物协议。

| 领域 | Skill 候选 | 依赖能力 | 首发状态 |
| --- | --- | --- | --- |
| 文献 | PubMed / PMC literature search | NCBI Entrez 或已审核 MCP | 能力联通后可用 |
| 预印本 | bioRxiv search | bioRxiv API / MCP | 能力联通后可用 |
| 蛋白注释 | UniProt lookup | UniProt API | 能力联通后可用 |
| 三维结构 | RCSB PDB search and review | RCSB API、结构查看器 | 能力联通后可用 |
| 预测结构 | AlphaFold DB lookup | AlphaFold DB API | 能力联通后可用 |
| 序列数据 | NCBI Datasets | NCBI Datasets API | 能力联通后可用 |
| 序列比对 | NCBI BLAST | 受控 BLAST 服务 | 能力联通后可用 |
| 基因组注释 | Ensembl lookup | Ensembl REST | 能力联通后可用 |
| 临床变异 | ClinVar interpretation | NCBI ClinVar | 能力联通后可用 |
| 人群变异 | gnomAD lookup | gnomAD API | 能力联通后可用 |
| 靶点发现 | Open Targets evidence | Open Targets API | 能力联通后可用 |
| 化学活性 | ChEMBL search | ChEMBL API | 能力联通后可用 |
| 化合物 | PubChem lookup | PubChem PUG REST | 能力联通后可用 |
| 通路 | Reactome pathway analysis | Reactome API | 能力联通后可用 |
| 相互作用 | STRING network analysis | STRING API | 能力联通后可用 |
| 单细胞 | CellxGene dataset discovery | CELLxGENE API | 能力联通后可用 |

目录中的条目只有在依赖通过健康检查、输入输出 schema 验证和授权检查后才标为“可用”。未接通能力可以显示为“待接入”，但不能让用户选择后创建一个注定失败或收费的 Run。

## 5. 导入流水线

每个候选 Skill 必须经过以下步骤，不能直接把 GitHub 仓库批量复制到生产目录：

1. 记录上游仓库、提交 SHA、原始路径、许可证、归属信息和抓取时间。
2. 校验 `SKILL.md` frontmatter、名称、描述和相对链接。
3. 拒绝路径穿越、设备文件、符号链接逃逸、过大文件、未知二进制和嵌入凭据。
4. 生成不可变包摘要；包文件存对象存储，PostgreSQL 保存版本、权限、来源和审核状态。
5. 将声明的工具 / MCP 依赖映射到 PSKit 服务端 capability ID；声明本身不授权。
6. 在隔离的 Staging 账号验证发现、加载、工具调用、产物和用量统计。
7. 管理员批准后加入公共目录；上游更新生成新版本，不能静默替换正在使用的版本。

## 6. 用户自定义 Skill 的产品边界

用户上传 ZIP 或目录后，默认创建本人可见的私有 Skill。Skill 包可以包含说明、参考资料和素材；脚本默认只保存和展示，不自动获得执行权限。用户可以更新私有版本、删除未被 Run 引用的草稿，并在自己的会话中选择固定版本。

推荐使用以下发布状态：

- `private`：仅作者可见和使用。
- `review_pending`：作者申请公开，等待安全、许可和能力依赖审核。
- `public`：审核通过，授权用户可在公共目录发现。
- `rejected`：审核未通过，仍保留作者私有副本和原因。
- `disabled`：平台因安全、许可或依赖问题停止后续加载。

如果选择“用户点击公开后立即全网可见”，就必须在上传链路同步完成恶意包扫描、秘密检测、许可证证明和工具权限审核。这个方案风险高且难以撤回，首版不推荐。

## 7. 与当前 PSKit 的差距

当前 `catalog_skill_versions` 只能保存单份 instructions 和 tools，`catalog_skill_grants` 只保存用户可用 ID；缺少：

- Skill 所有者、平台 / 用户来源和作者信息；
- 私有、待审核、公开、拒绝、禁用状态；
- 完整包、文件索引、对象存储引用和内容摘要；
- 许可证、上游提交、归属和导入审计；
- 会话级固定版本选择；
- 公开审核记录和管理员操作日志。

Composer 当前使用 `/` 选择 Skill、`@` 选择资源。若 `@` 同时承担 Skill 入口，推荐升级为统一 mention picker：默认展示 Skills，并提供 Files、Resources / MCP 分区；选择后写入结构化引用和独立 chip，不把 `@name` 留在普通消息字符串中。服务端必须重新解析并授权 skill ID、version 和 digest，不能信任浏览器提交的名称。

## 8. 建议实施顺序

1. 先确定公开发布是“立即公开”还是“管理员审核后公开”。
2. 写统一 Skill registry、包验证、对象存储与权限规格。
3. 确定 `@` 是统一 mention picker，还是把原资源入口迁到 `+` 菜单后让 `@` 只表示 Skill。
4. 实现后端目录、详情、上传、版本、会话选择和审核 API。
5. 实现 Skills 页面、上传流程和 Composer `@` 选择器。
6. 从许可明确的来源导入首批候选，并只发布已经接通依赖的条目。
7. 接入 Pi 的受控 `search_skills` / `load_skill`，记录每次发现、加载、工具触发和产物。

