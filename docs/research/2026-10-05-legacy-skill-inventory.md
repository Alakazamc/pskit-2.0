# 旧科研能力 → 新 Skill 目录盘点

日期：2026-10-05。范围：当前工作区源码；未查询生产数据库、旧容器内附带的上游 PSKit 安装包。

## 1. 实际数量与迁移状态

| 来源 | 可核验数量 | 含义 |
| --- | ---: | --- |
| `knowledge/agent_skills_and_tools.md` | 14 个清单名，另提及 `all-round_skill`，共 15 个不同名 | 文档中的技能路由约定；不是 15 份可安装的 `SKILL.md` |
| `backend/app/tools/catalog.py` | 28 个 `ToolSpec` | 工具调用入口；不能与上面的 Skill 数量相加 |
| `backend/app/harness/aptamer_skill.py` | 1 个 `aptamer_closed_loop`，11 个阶段 | 多数阶段复用上述工具 |
| `new_backend/skills/*/manifest.json` | 1 个：`structure-review` | 新系统已有文件型 Skill，依赖 `search_pdb` / `fetch_uniprot` |
| 新系统数据库注册的 Skill | 本轮未查询 | 不能仅凭文件盘点判断云端实际目录数量 |

**当前仓库不能证明“四十几个独立 Skill 已存在”。** 能确认旧系统有丰富能力，但它们采用了工具、知识文档、阶段三种描述方式。本设计完整列出这些来源，不补造技能数量。

目录迁移的验收对象是下面每一项的说明、输入条件、能力依赖、结果和证据要求。写好说明文件后仍须通过新计算服务契约、权限和可用性检查，才能标为可执行。界面设计稿中的目录条目是拟迁移内容，不表示这些服务已全部接通。

## 2. 28 个工具逐项映射

每项都取得稳定的 Skill ID，可独立检索、查看和选择。表中的“复用工具”意味着共享能力实现，避免复制执行代码。低层结果读取能力可作为辅助 Skill 展示在“结果与报告”分类，不混入默认推荐。

| # | 旧工具 | 拟定 Skill ID | 用户名称 / 任务 | 新系统接入要求 |
| --- | --- | --- | --- | --- |
| 1 | `search_pdb` | `pdb-discovery` | 蛋白结构检索 | 已有同名接口；校验真实提供者模式和返回证据 |
| 2 | `serpapi_search` | `web-research` | 网页与文献线索检索 | 注册网页搜索服务；来源链接进入证据 |
| 3 | `download_pdb_file` | `structure-download` | 下载结构文件 | 新文件所有权、结构格式和产物登记适配 |
| 4 | `search_uniprot` | `uniprot-search` | 蛋白序列检索 | 注册搜索接口；与 `fetch_uniprot` 的取条目语义区分 |
| 5 | `fetch_uniprot_entry` | `uniprot-inspection` | 蛋白条目解读 | 映射现有 `fetch_uniprot` 后验证契约 |
| 6 | `search_rnacentral` | `rnacentral-search` | RNA 序列检索 | 注册 RNAcentral 搜索能力 |
| 7 | `fetch_rnacentral_entry` | `rna-inspection` | RNA 条目解读 | 注册 RNAcentral 取条目能力 |
| 8 | `fetch_pdb_info` | `pdb-inspection` | 结构条目解读 | 注册 PDB 元数据接口 |
| 9 | `split_pdb_by_chain` | `chain-splitting` | 按链拆分结构 | 接收 owned artifact ID，不接收宿主机路径 |
| 10 | `split_complex` | `complex-splitting` | 拆分蛋白与核酸 | 同上，登记每个输出 |
| 11 | `extract_fragment` | `fragment-extraction` | 提取结构片段 | 链名 / 残基区间校验，来源关联 |
| 12 | `calculate_contact_map` | `contact-map` | 结构接触图 | 迁移计算规则；保留阈值与残基映射 |
| 13 | `annotate_binding_pairs` | `binding-contact-annotation` | 蛋白核酸接触注释 | 保留残基对、距离和来源结构 |
| 14 | `predict_binding_sites` | `binding-site-prediction` | DNA / RNA 结合位点预测 | 模型服务发布、GPU 配额、异步恢复 |
| 15 | `extract_empirical_features` | `structure-features` | 结构经验特征 | DSSP / 可选 Rosetta 依赖与参数记录 |
| 16 | `predict_interaction` | `sequence-interaction` | 蛋白核酸互作预测 | 模型服务、输入序列验证、任务结果 |
| 17 | `search_sequence_homologs` | `sequence-homology` | 序列同源分析 | MMseqs2 / BLASTP 执行适配、数据库版本 |
| 18 | `search_structure_homologs` | `structure-homology` | 结构同源分析 | Foldseek 服务与数据库版本 |
| 19 | `generate_coral_candidates` | `coral-rna-design` | CORAL RNA 设计 | 新通用计算 / MCP 服务；候选与来源登记 |
| 20 | `generate_pepccd_candidates` | `pepccd-peptide-design` | PepCCD 多肽设计 | 同上，独立服务权限和模型版本 |
| 21 | `score_research_candidates` | `candidate-ranking` | 候选评分排序 | 旧 `ResearchRun` 耦合改为 owned 输入产物 + 明确评分配置 |
| 22 | `submit_research_top10_af3` | `batch-af3-validation` | 候选批量结构验证 | 有序批次、单项幂等、配额准入和取消；复用 AF3 服务 |
| 23 | `run_alphafold3` | `af3-structure-prediction` | AlphaFold 3 预测 | 已有 `submit_af3` / 接收器链路；验证新 Skill 输入映射与真实部署能力 |
| 24 | `list_task_artifacts` | `task-result-discovery` | 查找任务结果 | 复用新任务 / 产物仓库，保留任务归属校验 |
| 25 | `read_result_file` | `result-reading` | 读取和解读结果 | 只读取用户有权访问的 artifact ID；文本与二进制预览分开 |
| 26 | `generate_session_report` | `session-report` | 对话研究报告 | 从新 Session / Run 证据生成，不复制旧会话模型 |
| 27 | `generate_harness_report` | `evidence-report` | 计算证据报告 | 旧 Harness 报告内容迁入新证据格式；不向用户暴露内部 Harness 概念 |
| 28 | `generate_research_report` | `research-report` | 科研报告 | 新 Run / candidate / artifact 证据适配；保留科学可追溯性 |

另有两个拟迁移入口：

| 来源 | Skill ID | 要求 |
| --- | --- | --- |
| `remote_rna_expert__generate_rna_for_protein`（旧知识文档列出） | `rna-expert-design` | 当前源码未证明远端可用；需接口 schema、服务发布与验收 |
| `aptamer_closed_loop` | `aptamer-design` | 将 11 阶段的领域指导、证据门槛、恢复建议写为 Skill；执行仍由统一 Task/Event 负责 |

因此这一版**提出 30 个迁移条目**（28 + 2），另保留新版 `structure-review` 综合入口。此数目是设计结果，不是已实现数量。`coral_mcp__predict`、`pepccd_mcp__generate` 是上述 CORAL / PepCCD 的远端别名，不再重复创建两张同功能卡片。

## 3. 旧 15 个 Skill 名称兼容表

旧 ID 只做显式 alias / bundle 映射；不把下划线全局替换为横线后假定意义相同。

| 旧名称 | 新入口 / 处理 |
| --- | --- |
| `pdb_discovery` | `pdb-discovery` |
| `web_search` | `web-research` |
| `structure_splitting` | `chain-splitting` + `complex-splitting` 的任务入口 |
| `binding_pair_annotation` | `binding-contact-annotation` |
| `fragment_extraction` | `fragment-extraction` |
| `get_contact_map` | `contact-map` |
| `binding_site_prediction` | `binding-site-prediction` |
| `feature_extraction_pipeline` | `structure-features` |
| `sequence_interaction_prediction` | `sequence-interaction` |
| `alphafold3_structure_prediction` | `af3-structure-prediction` |
| `rna_design_generation` | `rna-expert-design`；不得在未配置时替换成另一模型并声称等价 |
| `molecular_database_lookup` | PDB / UniProt / RNAcentral 检索入口的组合；保留旧 alias，不复制能力实现 |
| `introduce_tools` | 助手能力说明，由系统帮助处理，不默认占用户 Skill 卡片 |
| `default_fallback` | 助手澄清行为，不作为需用户开启的科研 Skill |
| `all-round_skill` | 跨领域路由约定，不注册能绕过权限的“全工具” Skill |

用户提及的“虚拟图样性分析”不是当前源码中的名称；设计按已存在的“序列同源分析 / 结构同源分析”分别列项，不声称它们与口述词完全等同。

## 4. `aptamer_closed_loop` 的 11 个阶段

| 阶段 | 指导与复用能力 |
| --- | --- |
| `target_analysis` | 取靶标元数据和结构 |
| `sequence_homology` | 序列同源检索 |
| `structure_homology` | 结构同源检索 |
| `binding_assessment` | 结合位点 / 互作预测，区分预测与实验验证 |
| `coral_candidates` | RNA 候选生成 |
| `pepccd_candidates` | 多肽候选生成 |
| `score_rna` | RNA 候选评分 |
| `score_peptide` | 多肽候选评分 |
| `top10_af3` | 排名候选的 AF3 验证 |
| `strategy_feedback` | 持久化结果评价；旧代码中 `record_strategy_feedback` 为内部 legacy 占位，需要新实现 |
| `report` | 科研报告 |

阶段顺序、前置证据、参数和可恢复点进入 Skill 文档与参考文件；GPU 调度、checkpoint、取消、重试、结果写入继续由平台运行时负责。不得仅复制 YAML/Markdown 就标记“闭环已迁移”。

## 5. 每项迁移的核对清单

1. 从旧源码提取操作方法、输入、结果含义和失败条件，写入 `SKILL.md`，不是只有一个工具名。
2. 核实服务 schema、模型 / 数据库版本、许可证和 CPU/GPU usage 声明。
3. 将工具依赖绑定至新系统发布的服务版本；即刻完成 / 后台任务都返回统一协议。
4. 在受控会话中验证结果文件、配额、任务恢复和取消；没有真实服务时只标待接入。
5. 登记不可变 Skill 版本、包 digest、来源、可用性和依赖；逐项开放。
6. 保留旧 ID 兼容映射及真实证据，不保留旧多重 Registry / Harness 执行链。

## 源码依据

- [旧 Skill 与工具约定](../../knowledge/agent_skills_and_tools.md)
- [旧工具目录](../../backend/app/tools/catalog.py)
- [旧工具工作流](../../knowledge/tool_workflows.md)
- [旧适配体 Skill](../../backend/app/harness/aptamer_skill.py)
- [旧阶段注册契约](../../backend/app/harness/skill_registry.py)
- [新版已有 Skill](../../new_backend/skills/structure-review/SKILL.md)
- [新版 Skill 注册与权限](../../new_backend/app/domain/catalog.py)

计数方式：对旧 `ToolSpec` / `StageSpec` 做 AST 静态盘点，对新版 `manifest.json` 做文件盘点；未导入运行应用，未触发模型调用。
