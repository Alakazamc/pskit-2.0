# AlphaFold 3 Support and Limits / AlphaFold3 支持与限制

## Current integration / 当前集成
PSKit includes AlphaFold 3 through the Web UI task `af3_predict` and the Agent tool `run_alphafold3`. AF3 is a heavy GPU and Docker-backed job. Use it only when the user explicitly asks for sequence-based structure prediction, not for simple PDB lookup or metadata retrieval.

## Web UI support / 网页支持
The current AlphaFold 3 Web UI panel is protein-sequence oriented. It accepts job name, protein sequences, model seed, diffusion sample count, template date, and GPU device. The panel validates up to 8 protein chains and sequence length up to 5000 residues per sequence.

## Agent and backend entity support / Agent 与后端 entities
The Agent tool path prefers `entities`. Backend parsing in `pskit/ai/alphafold.py` accepts entity type `protein` or `rna`. Each entity has `type` and `sequence`. `protein_sequences` remains a backward-compatible path that becomes protein entities.

## Precise wording / 准确表述
Be precise: the Web UI is currently protein-sequence focused, while the backend/tool path has entity parsing for protein and RNA. Do not promise that every mixed protein-RNA AF3 workflow is fully validated through the UI. For mixed complexes, prefer the Agent/tool path and warn that runtime success depends on AF3 installation and input compatibility.

## Runtime requirements / 运行要求
AF3 requires Docker, GPU access, `PSKIT_AF3_IMAGE`, `PSKIT_AF3_DB_DIR`, `PSKIT_AF3_MODEL_DIR`, `PSKIT_AF3_GPU_DEVICE`, and `PSKIT_AF3_LOCK_FILE`. `PSKIT_AF3_EXTRA_ARGS` can append extra command arguments. Defaults may include `/home/public/database/alphafold3` and `/data/hzeng/af3/model-parameters`, but environment variables are authoritative.

## Tool arguments / 工具参数
Important `run_alphafold3` arguments are `entities`, `protein_sequences`, `job_name`, `model_seed`, `num_diffusion_samples`, `gpu_device`, and `max_template_date`.

## Output files / 输出文件
Typical outputs include `af3_input.json`, `af3_command.txt`, `af3_stdout.log`, `af3_stderr.log`, `af3_outputs_manifest.json`, generated structure/confidence files, and `error.json` on failures. Read AF3 logs only for troubleshooting or when the tool result indicates an error.

## Common failures / 常见失败
Common failures include missing AF3 database, missing AF3 model directory, Docker image unavailable, Docker permission denied, invalid sequence characters, invalid entity type, GPU unavailable, lock contention, out-of-memory, and long runtime.
