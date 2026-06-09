# Deployment and Runtime / 部署与运行时

## Active A6000 deployment / 当前部署
The active deployment is native under `/data1/kxchen/pskit`. The webserver commonly runs as `pskit-webserver /data1/kxchen/pskit 127.0.0.1:10706 2` and serves both API routes and `webpage/dist`.

## Chroma runtime / Chroma 运行时
Vector RAG expects a Chroma HTTP server, typically `http://127.0.0.1:8000`, and a persistent data directory such as `tasks/chroma`. Chroma data is generated runtime state and should not be committed.

## Embedding runtime / Embedding 运行时
Embedding calls use an OpenAI-compatible `/embeddings` endpoint. A6000 should use direct campus-network access to SiliconFlow `/embeddings` for vector RAG instead of local forwarding proxies.

## Docker image / Docker 镜像
The Dockerfile builds Rust binaries, WASM package, frontend assets, and Python runtime. Model parameters and Chroma data need external mounts or runtime setup.

## External services / 外部服务
PSKit can depend on RCSB, UniProt, RNAcentral, OpenAI-compatible chat/embedding endpoint, Chroma server, and remote RNA expert MCP. Restricted networks may require proxies and SSH tunnels.

## Important paths / 关键路径
Source repo: `/data1/kxchen/pskit`. Agent config: `agent_config/`. RAG corpus: `agent_config/knowledge/*.md`. Frontend: `webpage/dist`. Python AI: `pskit/ai/`. Rust webserver: `webserver/`. Toolkit: `pskit/toolkit/`. Task state: `tasks/`.
