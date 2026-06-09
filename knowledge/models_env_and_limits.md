# Models, Environment Variables, and Limits / 模型、环境变量与限制

## Model directory / 模型目录
`PSKIT_MODEL_PARAMETERS` controls the base model parameter directory. Docker images may not include model parameters, so mount or provide them separately.

## Models / 模型
INABe supports `pred_nbs` and `predict_binding_sites`. PAIR supports `pred_pni` and `predict_interaction`. ESM-2 650M/150M supports protein embeddings and features. SaProt depends on Foldseek. RNA-FM supports RNA representation. AlphaFold 3 requires Docker, GPU, databases, and model parameters.

## Binary dependencies / 二进制依赖
`PSKIT_FOLDSEEK` controls Foldseek. `PSKIT_DSSP` controls mkdssp/DSSP. Rosetta needs executables and `ROSETTA3_DB`. `PSKIT_PYTHON` can override the Python runtime.

## AF3 variables / AF3 变量
AF3 variables include `PSKIT_AF3_DB_DIR`, `PSKIT_AF3_MODEL_DIR`, `PSKIT_AF3_IMAGE`, `PSKIT_AF3_GPU_DEVICE`, `PSKIT_AF3_LOCK_FILE`, and `PSKIT_AF3_EXTRA_ARGS`.

## RAG and embeddings / RAG 与 embedding
Vector RAG uses Chroma and API embeddings. Important config values are embedding API URL, embedding model, Chroma URL, collection name, tenant, database, and API key env such as `SILICONFLOW_API_KEY`.

## OpenAI-compatible proxy / LLM 代理
Related variables include `OPENAI_API_KEY` for chat, `SILICONFLOW_API_KEY` for RAG embeddings, and direct provider URL settings in `agent_config/config.toml`.

## RCSB variables / RCSB 变量
RCSB configuration includes `PSKIT_RCSB_SEARCH_URL`, `PSKIT_RCSB_FILES_BASE`, `PSKIT_RCSB_DATA_BASE`, `RCSB_PROXY_HOST`, and `RCSB_PROXY_PORT`.

## SerpAPI variables / SerpAPI 变量
Public web search uses `SERPAPI_API_KEY`. `PSKIT_SERPAPI_SEARCH_URL` can override the default SerpAPI endpoint for debugging or proxying.

## Important limits / 重要限制
Upload limit is 250 MB. Some ESM-2/SaProt paths reject sequences over 1000 residues. Web UI AF3 accepts up to 8 protein chains and sequence length up to 5000. `read_result_file` only reads text under Agent session artifacts. AF3 is GPU/Docker-heavy. Rosetta relax is opt-in only.
