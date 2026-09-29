# PSKit Tool Workflows / 工具工作流

## Principles / 原则

Use the smallest workflow that satisfies the request. Search before download,
reuse returned artifact IDs, and avoid expensive prediction unless explicitly
requested. Explain that computational predictions need experimental validation.

## Structure lookup and analysis / 结构检索与分析

Use `search_pdb`, optionally `fetch_pdb_info`, then `download_pdb_file`. The
download returns a registered artifact. Pass its `artifact_id` to
`split_pdb_by_chain`, `split_complex`, `extract_fragment`,
`calculate_contact_map`, or `annotate_binding_pairs`.

## Prediction / 预测

Use `predict_binding_sites` with an owned structure artifact and `DNA` or `RNA`.
Use `predict_interaction` for a protein and nucleic-acid sequence pair. Use
`extract_empirical_features` for DSSP or optional legacy feature extraction.
These operations are queued for the background worker.

## AlphaFold 3 and remote MCP / AF3 与远程 MCP

Run `run_alphafold3` only after explicit confirmation because it can require a
licensed database, model parameters, Docker, and GPU resources. Use
`remote_rna_expert__generate_rna_for_protein` only when its MCP service URL is
configured and reachable. CORAL and PepCCD use `coral_mcp__predict` and
`pepccd_mcp__generate`; pass the selected server tool's input inside
`arguments`.

## Reports and results / 报告与结果

Use `read_result_file` only with a known owned `artifact_id`. Use
`generate_session_report` to create a registered Markdown summary. Download
binary or large artifacts through the file API.
