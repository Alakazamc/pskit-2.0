# Current PSKit Inventory

## Current Deployment

Current PSKit is deployed on A6000:

```text
/data1/kxchen/pskit
```

Current internal URL:

```text
http://172.31.199.38:10706/agent
```

The current server is native A6000 deployment rather than a long-lived Docker-only deployment.

## Current Major Directories

| Path | Meaning |
| --- | --- |
| `webserver/` | Rust/Axum backend, Agent loop, auth, tasks, doctor |
| `webpage/` | Vue/Vite frontend |
| `pskit/ai/` | Python AI runtime: INABe, PAIR, AlphaFold wrapper |
| `pskit/toolkit/` | Rust toolkit and WASM-related code |
| `agent_config/` | Agent model config, skills, tools, MCP, knowledge |
| `agent_config/knowledge/` | Markdown RAG knowledge files |
| `scripts/` | doctor, Chroma index builder, proxy and deployment scripts |
| `tasks/` | SQLite DB, Chroma runtime data, task results, session files |

## Current Agent Model Configuration

Current chat model:

```text
deepseek-v4-flash
```

Current chat API:

```text
https://api.deepseek.com/v1/chat/completions
```

Current embedding model:

```text
BAAI/bge-m3
```

Current embedding API:

```text
https://api.siliconflow.cn/v1/embeddings
```

Current RAG backend:

```text
Chroma collection: pskit_knowledge
```

## Current Knowledge Files

The current project has these knowledge files:

```text
agent_skills_and_tools.md
alphafold3_limits.md
bioinformatics_concepts.md
database_lookup.md
deployment_and_runtime.md
models_env_and_limits.md
output_artifacts_reference.md
pskit_capabilities.md
tool_workflows.md
troubleshooting.md
web_ui_and_task_api.md
```

PSKit 2.0 should migrate these into a new `knowledge/` directory and index them into Qdrant.

## Current Tools

Tool catalog to preserve:

- `search_pdb`
- `serpapi_search`
- `download_pdb_file`
- `search_uniprot`
- `fetch_uniprot_entry`
- `search_rnacentral`
- `fetch_rnacentral_entry`
- `fetch_pdb_info`
- `split_pdb_by_chain`
- `split_complex`
- `extract_fragment`
- `calculate_contact_map`
- `annotate_binding_pairs`
- `predict_binding_sites`
- `extract_empirical_features`
- `predict_interaction`
- `run_alphafold3`
- `read_result_file`
- `remote_rna_expert__generate_rna_for_protein`

## Current Skills To Preserve

- PDB Discovery
- Web Search
- Structure Splitting
- Protein Nucleic Acid Binding Pair Annotation
- Chain Fragment Extraction
- Get Contact Map
- Binding Site Prediction
- Feature Extraction Pipeline
- Sequence Interaction Prediction
- AlphaFold 3 Structure Prediction
- RNA Design Generation
- Molecular Database Lookup
- All-Round Skill
- Introduce Tools

## Current Model Runtime Dependencies

| Dependency | Purpose |
| --- | --- |
| `INABe_RNA.pth` | RNA binding site prediction |
| `INABe_DNA.pth` | DNA binding site prediction |
| `ESM2 650M` | protein embedding for binding site pipeline |
| `ESM2 150M` | sequence interaction pipeline |
| `SaProt_650M_PDB` | structure-aware protein embedding |
| `RNA-FM_pretrained.pth` | nucleic-acid embedding |
| `pair_pskit.pt` | interaction model |
| `Foldseek` | structure token / SaProt feature preparation |
| `DSSP / mkdssp` | secondary-structure features |
| `AlphaFold 3 image/db/model params` | structure prediction |

## Lessons From Current Version

- Runtime doctor is essential.
- Agent must understand tool boundaries.
- Long jobs need explicit task states.
- File ownership must be enforced server-side.
- RAG should include citations and stale-index checks.
- Environment-specific paths must be configured, not hard-coded.
- A6000 should run independently from the local laptop.

