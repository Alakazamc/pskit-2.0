# Molecular Database Lookup / 分子数据库查询

## Overview / 总览
PSKit uses RCSB PDB for structures, UniProt for protein sequence and annotation, and RNAcentral for RNA sequence and annotation. Use lookup tools when the user starts from a name, accession, gene, organism, structure keyword, RNA family, or vague biological description.

## RCSB PDB / 结构数据库
Use RCSB for experimental structures, PDB IDs, chains, resolution, organism metadata, and structure files. Agent tools are `search_pdb`, `fetch_pdb_info`, and `download_pdb_file`. Search first, fetch metadata only when needed, then download only when a downstream structure workflow needs a local file.

## RCSB workflow / RCSB 工作流
Recommended flow: `search_pdb` for candidates, `fetch_pdb_info` for chain/entity context, and `download_pdb_file` for actual analysis. Prefer `cif` unless the user requests PDB or a tool requires PDB. Do not re-download if a session already has the file.

## UniProt / 蛋白数据库
Use UniProt for protein names, accessions, genes, species, sequence-level metadata, and functional annotations. Agent tools are `search_uniprot` and `fetch_uniprot_entry`. UniProt does not automatically provide a 3D structure; use RCSB separately if a structure is required.

## RNAcentral / RNA 数据库
Use RNAcentral for RNA symbols, accessions, RNA families, species-specific RNA entries, and RNA sequence metadata. Agent tools are `search_rnacentral` and `fetch_rnacentral_entry`.

## Public web search / 公共网页搜索
Use SerpAPI only for current external context, papers, project/company/tool background, and documentation that is not covered by RCSB, UniProt, or RNAcentral. Agent tool: `serpapi_search`. Prefer authoritative molecular databases for biological identifiers and structures. Web search results should be cited by URL and should not be treated as proof that a local PSKit analysis completed.

## Database choice / 数据库选择
Use RCSB for 3D structure questions, UniProt for protein sequence and protein annotation, and RNAcentral for RNA sequence and RNA annotation. For multi-entity workflows, lookup sequence metadata first and use RCSB only if structure analysis is required.

## Proxy and network / 代理与网络
RCSB, UniProt, RNAcentral, and SerpAPI may fail on restricted networks. RCSB-related variables include `PSKIT_RCSB_SEARCH_URL`, `PSKIT_RCSB_FILES_BASE`, `PSKIT_RCSB_DATA_BASE`, `RCSB_PROXY_HOST`, and `RCSB_PROXY_PORT`. SerpAPI requires `SERPAPI_API_KEY` and can optionally override `PSKIT_SERPAPI_SEARCH_URL`. If lookups fail on A6000, check network reachability, campus authentication, provider key, proxy, or tunnel availability.

## Result interpretation / 结果解释
Search tools return candidates, not final biological truth. When multiple entries are plausible, summarize the top hits and ask the user to choose. Metadata tools may return raw JSON; summarize relevant fields instead of dumping full JSON.
