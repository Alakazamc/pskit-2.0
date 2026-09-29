# AlphaFold 3 Support and Limits / AlphaFold 3 支持与限制

PSKit exposes AlphaFold 3 through the Agent tool and background task named
`run_alphafold3`. Use it only for an explicit structure-prediction request, not
for PDB lookup or metadata retrieval.

Input contains 1–32 protein, RNA, or DNA entities, with a maximum combined
sequence length of 20,000 characters. Diffusion samples must be between 1 and
20. Runtime availability still depends on the configured AlphaFold 3 image,
licensed database and model directories, compatible input, Docker permissions,
GPU access, memory, and execution timeout.

The core PSKit image does not bundle AlphaFold 3, its weights, or databases.
Configure `PSKIT_AF3_IMAGE`, `PSKIT_AF3_DB_DIR`, `PSKIT_AF3_MODEL_DIR`, and
`PSKIT_AF3_GPU_DEVICE` only after installing and licensing those assets. Run the
executor separately from the public web container; do not mount the Docker
socket into the web service.

Typical registered outputs include the AF3 input, command and bounded logs,
output manifest, predicted structures, confidence data, and error reports.
Prediction quality must be assessed independently and experimentally validated.
