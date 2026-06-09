# PSKit Tool Workflows / 工具工作流

## Principles / 原则
Use the smallest workflow that satisfies the user request. Search before download, download before structure analysis, and read only safe text result files. Avoid AF3 and Rosetta relax unless explicitly requested.

## Find and download PDB / 查找并下载 PDB
Use `search_pdb` for candidates, `fetch_pdb_info` for metadata comparison, and `download_pdb_file` for local analysis. Prefer `cif` unless PDB is required.

## Split and extract / 拆分与提取
For all chains use `split_pdb_by_chain`. For molecule-type separation use `split_complex`. Use `extract_fragment` when the user specifies chain, start, or end residue.

## Contact map / 接触图
Use `calculate_contact_map` with structure path, format, optional chain, and optional mode. Explain that output JSON contains residue axis labels and contact values.

## Binding workflows / 结合相关工作流
Use `annotate_binding_pairs` for geometry-based protein-nucleic-acid contacts. Use `predict_binding_sites` with `ligand_type` `DNA` or `RNA` for ML residue prediction. Use `predict_interaction` or task `pred_pni` for sequence-pair interaction prediction.

## Feature and embedding workflows / 特征与嵌入
Use `extract_empirical_features` for DSSP and optional Rosetta. Keep `rosetta_relax=false` unless requested. Use `lm_embed` with `model_type` `esm2`, `saprot`, or `both`; `.npy` outputs are binary arrays.

## AlphaFold 3 / AF3
Confirm explicit user intent, warn about GPU/Docker cost, run `run_alphafold3`, and inspect `af3_stdout.log`, `af3_stderr.log`, `af3_command.txt`, or `error.json` only when troubleshooting.

## RNA design / RNA 设计
Identify PDB ID and chain, use metadata if needed, then call `remote_rna_expert__generate_rna_for_protein` if the MCP service is available.

## Result reading / 结果读取
Use `read_result_file` for known CSV, JSON, log, TXT, or Markdown paths returned by tools. Download binary or large files instead.
