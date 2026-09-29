# Agent Skills and Tools / Agent 技能与工具

## Skill routing / 技能路由
The Agent selects one skill and only uses tools allowed by that skill. Use a specific skill when possible. Use `all-round_skill` only for multi-step workflows that cross categories. Use `default_fallback` for unclear or out-of-scope requests.

## Skill inventory / 技能清单
`pdb_discovery` finds PDB entries. `web_search` searches the public web through SerpAPI. `structure_splitting` splits structures. `binding_pair_annotation` annotates contact pairs. `fragment_extraction` extracts chains or residue intervals. `get_contact_map` computes contact maps. `binding_site_prediction` predicts DNA/RNA-binding residues. `feature_extraction_pipeline` extracts DSSP/Rosetta features. `sequence_interaction_prediction` predicts protein-nucleic-acid interaction. `alphafold3_structure_prediction` runs AF3. `rna_design_generation` uses the remote RNA expert. `molecular_database_lookup` queries RCSB/UniProt/RNAcentral. `introduce_tools` explains capabilities. `default_fallback` asks clarifying questions.

## Tool inventory / 工具清单
Database tools: `search_pdb`, `fetch_pdb_info`, `search_uniprot`, `fetch_uniprot_entry`, `search_rnacentral`, `fetch_rnacentral_entry`. Web search tool: `serpapi_search`. Structure tools: `download_pdb_file`, `split_pdb_by_chain`, `split_complex`, `extract_fragment`, `calculate_contact_map`, `annotate_binding_pairs`. AI tools: `predict_binding_sites`, `extract_empirical_features`, `predict_interaction`, `run_alphafold3`. Result tool: `read_result_file`. Remote MCP tools: `coral_mcp__predict`, `pepccd_mcp__generate`, `remote_rna_expert__generate_rna_for_protein`.

## `web_search`
Use for current external context, papers, tool/company/background lookup, and documentation that is not available in PSKit's molecular databases. Tool: `serpapi_search`. Always summarize with source URLs and separate web evidence from PSKit computation results.

## `binding_site_prediction`
Use for predicted DNA-binding or RNA-binding residues from structure. Tools: `predict_binding_sites`, `download_pdb_file`, `read_result_file`. Key argument: `ligand_type` is `DNA` or `RNA`.

## `alphafold3_structure_prediction`
Use only for explicit sequence-based AF3 structure prediction. Tools: `run_alphafold3`, `read_result_file`. Warn that AF3 is heavy and may wait for GPU or lock resources.

## `rna_design_generation`
Use for candidate RNA generation through remote MCP. Tools: `search_pdb`, `fetch_pdb_info`, `remote_rna_expert__generate_rna_for_protein`. Availability depends on the MCP service.

## CORAL and PepCCD MCP
Use `coral_mcp__predict` or `pepccd_mcp__generate` only when the corresponding
service is configured. Put the exact arguments required by the selected remote
MCP tool inside the `arguments` object. These calls are queued and their JSON
responses become registered artifacts.

## Result tool / 结果工具
`read_result_file` reads UTF-8 text artifacts under the Agent session directory. It should use paths returned by tools and should not guess arbitrary server paths.
