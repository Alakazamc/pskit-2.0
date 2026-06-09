# PSKit Troubleshooting / 故障排查

## Identify the failing layer / 定位故障层
First identify whether the failure is in the Web UI, Rust webserver, backend Python task queue, Agent model endpoint, Chroma RAG, external database/proxy, model weights, binary dependencies, GPU, Docker, or AF3 resources.

## Agent does not answer / Agent 不回复
The Agent depends on the configured OpenAI-compatible chat endpoint, configured as `https://v2.pincc.ai/v1/chat/completions`. If no process listens on that port, the page can load but chat may hang or fail. Check campus-network authentication, direct provider reachability, `OPENAI_API_KEY`, and the configured chat API URL.

## Chroma RAG failure / Chroma RAG 失败
Vector RAG depends on three pieces: the embedding endpoint, the Chroma HTTP server, and a built collection. If embeddings or Chroma fail, PSKit should fall back to keyword Markdown retrieval when `fallback_to_keyword = true`. Check Chroma URL, collection name, embedding model, API key env, and whether `scripts/build_chroma_index.py --reset` was run.

## RAG knowledge not retrieved / RAG 知识没有命中
The canonical corpus is `agent_config/knowledge/*.md`. Chroma must be rebuilt after knowledge files change. The keyword fallback only reads Markdown direct children unless code is changed. Use clear headings with tool names, task names, route names, model names, and Chinese/English aliases.

## Database failures / 数据库失败
RCSB, UniProt, and RNAcentral can fail due to invalid IDs, no hits, network restrictions, timeout, or proxy issues. For RCSB check `PSKIT_RCSB_SEARCH_URL`, `PSKIT_RCSB_FILES_BASE`, `PSKIT_RCSB_DATA_BASE`, `RCSB_PROXY_HOST`, and `RCSB_PROXY_PORT`.

## SerpAPI web search failure / SerpAPI 网页搜索失败
`serpapi_search` requires `SERPAPI_API_KEY`. Common failures are missing key, exhausted quota, invalid query, upstream timeout, campus-network restriction, or provider-side rate limit. Do not retry aggressively if SerpAPI reports quota/rate-limit errors.

## Remote RNA expert MCP failure / 远程 RNA 专家失败
The remote RNA expert tool depends on an MCP SSE service. If discovery fails, `remote_rna_expert__generate_rna_for_protein` may be unavailable. Causes include service down, unreachable SSE URL, network route issues, discovery timeout, JSON-RPC error, or tool-level `isError`.

## Task stuck pending / 任务 Pending
Backend tasks use an in-memory queue and worker semaphore. Check that `pskit-webserver` is running, worker count is nonzero, no heavy AF3/Rosetta jobs are occupying workers, SQLite `tasks/tasks.db` is accessible, and the Python runtime from `PSKIT_PYTHON` works.

## Upload or input failure / 上传或输入失败
The upload body limit is 250 MB. Invalid PDB/mmCIF, missing chains, unsupported residue numbering, malformed sequence JSON, invalid sequence characters, or files too large can cause failures.

## Missing models or binaries / 缺少模型或二进制
AI tasks need local model parameters under `PSKIT_MODEL_PARAMETERS`. SaProt needs `PSKIT_FOLDSEEK`; DSSP needs `PSKIT_DSSP` or `mkdssp`; Rosetta needs executables and `ROSETTA3_DB`; AF3 needs Docker, GPU, image, database, and model paths.

## read_result_file failure / 结果读取失败
`read_result_file` can read only regular UTF-8 text files under Agent session artifacts. It cannot read arbitrary server files, secrets, binary `.npy` arrays, image files, model weights, or guessed paths outside returned tool outputs.

## Native versus Docker / 原生与 Docker
The active A6000 deployment is native under `/data1/kxchen/pskit`. Docker build support exists, but do not assume the live service runs in Docker. Paths and proxy behavior differ between native and Docker deployment.
