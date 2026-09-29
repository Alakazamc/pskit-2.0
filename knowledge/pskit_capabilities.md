# PSKit Capabilities / PSKit 功能总览

## Product scope / 产品定位

PSKit 2.0 is a FastAPI and Vue workbench for structural bioinformatics and
protein–nucleic-acid workflows. It combines authenticated Agent sessions,
database-backed background tasks, registered artifacts, Qdrant/keyword RAG,
public molecular databases, and optional local or remote model tools. It is not
a clinical system and predictions require experimental validation.

## User interface / 用户界面

The current pages are Home, Login/Register, Agent, Tasks, Tools, and the
administrator runtime doctor. Agent sessions and task history are isolated by
user. The first account is administrator in the default deployment mode.

## Lightweight tools / 轻量工具

Available synchronous tools include RCSB PDB search/metadata/download, UniProt,
RNAcentral, optional SerpAPI search, structure splitting, fragment extraction,
contact maps, binding-pair annotation, registered text-result reading, and
session report generation.

## Background tasks / 后台任务

Long-running task names are `predict_binding_sites`, `predict_interaction`,
`extract_empirical_features`, `run_alphafold3`, `coral_mcp__predict`,
`pepccd_mcp__generate`, and
`remote_rna_expert__generate_rna_for_protein`. Tasks are persisted in SQL,
claimed atomically by the worker, and recovered after interrupted executions.

## Optional runtime / 可选运行时

Binding prediction, empirical feature extraction, and AlphaFold 3 require
external runtimes, binaries, model weights, and sometimes a GPU. Those assets
are not bundled in the core image because of size and licensing. The web app,
authentication, task history, database lookups, keyword RAG, and lightweight
structure processing work without them.

## Safety boundaries / 安全边界

The Agent can invoke only catalogued tools. Task inputs are size- and
type-validated. Structure operations use owned artifact IDs, and file downloads
enforce ownership. PSKit does not provide arbitrary shell or filesystem access.
