# PSKit Capabilities / PSKit 功能总览

## Product scope / 产品定位
PSKit is a web and agent system for structural bioinformatics and protein-nucleic-acid workflows. It combines a Rust webserver, Vue frontend, browser/WASM structure tools, Python AI tasks, local model parameters, external biological databases, Chroma-based RAG, and an LLM Agent with tool calling.

PSKit is best for exploratory research workflows, task orchestration, model-assisted analysis, output interpretation, and explaining how to use PSKit. It is not a clinical decision system, not a substitute for experimental validation, and not a general-purpose shell for arbitrary server operations.

## Execution modes / 执行模式
PSKit has four execution modes: Web UI pages, Agent chat, backend Python tasks, and browser/WASM tools. Web UI pages are best for explicit form-based tasks. Agent chat is best for natural-language workflows and tool orchestration. Backend Python tasks run AI models and long computations. Browser/WASM tools run local structure manipulation without consuming backend Python workers.

## Supported inputs / 支持输入
Common inputs include PDB IDs, PDB files, mmCIF files, protein sequences, DNA/RNA sequences, and protein-nucleic-acid complexes. Structure workflows usually need PDB/mmCIF. Sequence workflows need validated protein or nucleic-acid sequences. Database lookup workflows may start from names, accessions, genes, organisms, or free-text descriptions.

## Main capabilities / 主要能力
PSKit supports PDB discovery, RCSB metadata lookup, structure download, UniProt lookup, RNAcentral lookup, SerpAPI public web search, chain splitting, molecule-type splitting, fragment extraction, contact-map generation, protein-nucleic-acid binding-pair annotation, DNA/RNA binding-site prediction, DSSP and optional Rosetta feature extraction, ESM-2/SaProt embeddings, protein-nucleic-acid interaction prediction, AlphaFold 3 task launch, remote RNA design via MCP, Agent session files, result reading, and discussion report generation.

## Agent capabilities / Agent 能力
The Agent selects a skill, retrieves PSKit knowledge, calls only allowed tools, and summarizes outputs. It should use specific skills when possible, avoid unnecessary metadata fetches, avoid re-downloading existing structures, avoid AlphaFold 3 unless explicitly requested, and avoid Rosetta relax unless the user explicitly asks.

## Web UI pages / 网页入口
Important routes are `/`, `/agent`, `/binding/nbsa`, `/binding/nbsp`, `/binding/pnip`, `/features/structural`, `/features/language-model`, `/features/alphafold3`, `/tools/split`, `/tools/extract`, `/contact-map`, `/viewer`, `/about/guide`, and `/about/technical`.

## Backend task types / 后端任务类型
Backend task types are `pred_nbs` for nucleic-acid binding-site prediction, `pred_pni` for protein-nucleic-acid sequence interaction prediction, `emp_feats` for empirical structural features, `lm_embed` for language-model embeddings, and `af3_predict` for AlphaFold 3 prediction.

## Browser and CLI tools / 浏览器与 CLI 工具
Browser/WASM and `pskit-cli` capabilities include split-by-chain, split-complex, extract-fragment, contact-map, and annotate-binding-pairs. Browser execution is interactive and local to the client. Agent/backend execution stores artifacts in server-side task or session directories.

## Deployment fact / 当前部署事实
The active deployment is native on the A6000 server at `/data1/kxchen/pskit`. `pskit-webserver` serves the frontend from `webpage/dist` and usually binds to `127.0.0.1:10706`. The Dockerfile exists, but do not claim the live service is a long-running PSKit container unless that has changed.
