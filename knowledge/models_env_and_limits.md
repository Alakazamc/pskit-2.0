# Models, Environment Variables, and Limits / 模型、环境变量与限制

## Providers / 服务配置

Chat uses `LLM_BASE_URL`, `LLM_MODEL_ID`, and `LLM_API_KEY`. Embeddings use
`EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, and `EMBEDDING_API_KEY`. Qdrant uses
`QDRANT_URL`, optional `QDRANT_API_KEY`, `QDRANT_COLLECTION`, and vector size and
distance settings. SerpAPI and the remote RNA expert are optional.

## Heavy runtime / 重型运行时

`PSKIT_LEGACY_ROOT` and `PSKIT_MODEL_PARAMETERS` locate an optional compatible
legacy runtime and model directory. `PSKIT_FOLDSEEK` and `PSKIT_DSSP` locate
external binaries. AlphaFold 3 uses `PSKIT_AF3_DB_DIR`,
`PSKIT_AF3_MODEL_DIR`, `PSKIT_AF3_IMAGE`, and `PSKIT_AF3_GPU_DEVICE`. These
assets are not included in the core image.

The current task API runs INABe, PAIR, and AlphaFold 3 through the local science
worker. CORAL and PepCCD run through separately managed MCP/SSE services. Set
`CORAL_MCP_SSE_URL` / `CORAL_MCP_TOOL_NAME` and
`PEPCCD_MCP_SSE_URL` / `PEPCCD_MCP_TOOL_NAME`. A tool name may be omitted only
when that MCP server exposes exactly one tool; otherwise PSKit fails closed and
reports the available names instead of guessing which remote operation to run.

## Input and execution limits / 输入与执行限制

Serialized task input is limited to 1 MB and total sequence input to 20,000
characters. AlphaFold 3 accepts at most 32 entities and 1–20 diffusion samples.
Remote RNA generation accepts 1–50 samples. Subprocess, LLM stream, remote MCP,
retry, and stale-task timeouts are configurable and finite.

## Result access / 结果访问

Tools consume registered artifact IDs rather than server paths. Text preview is
bounded and restricted to owned UTF-8 artifacts. Model outputs and binary files
should be downloaded instead of read as text.
