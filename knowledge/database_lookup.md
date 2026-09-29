# Molecular Database Lookup / 分子数据库查询

Use RCSB PDB for experimental structures, PDB IDs, chains, resolution, metadata,
and structure files. The normal flow is `search_pdb`, optional
`fetch_pdb_info`, then `download_pdb_file` only when analysis needs a local
artifact. Prefer mmCIF unless PDB is specifically required.

Use `search_uniprot` and `fetch_uniprot_entry` for protein names, accessions,
genes, organisms, sequences, and functional annotations. Use
`search_rnacentral` and `fetch_rnacentral_entry` for RNA accessions, families,
organisms, and sequence metadata. These databases return candidates and
annotations, not experimental proof for a new biological claim.

Use `serpapi_search` only for current public context not covered by the molecular
databases. It requires `SERPAPI_API_KEY`. RCSB, UniProt, and RNAcentral endpoint
variables are `RCSB_*`, `UNIPROT_*`, and `RNACENTRAL_*` as shown in the
environment template. Network, proxy, timeout, quota, and identifier errors
should be reported clearly without aggressive retrying.
