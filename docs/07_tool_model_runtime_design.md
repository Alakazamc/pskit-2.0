# Tool and Model Runtime Design

## Tool Adapter Pattern

Each tool should be a Python adapter:

```text
tools/
  pdb.py
  uniprot.py
  rnacentral.py
  structure.py
  binding_sites.py
  interaction.py
  alphafold3.py
  mcp_remote_rna.py
  reports.py
```

Each adapter must:

- Define Pydantic input schema.
- Validate ownership and paths.
- Run preflight dependency checks.
- Execute the tool or submit a Celery task.
- Register artifacts.
- Return structured result or structured error.

## Model Registry

Create a single model registry:

```yaml
binding_site:
  rna:
    weight: INABe_RNA.pth
    requires:
      - esm2_650M
      - SaProt_650M_PDB
      - foldseek
  dna:
    weight: INABe_DNA.pth
    requires:
      - esm2_650M
      - SaProt_650M_PDB
      - foldseek

interaction:
  pair:
    weight: pair_pskit.pt
    requires:
      - esm2_150M
      - rna-fm/RNA-FM_pretrained.pth

alphafold3:
  image: alphafold3:3.0.1
  requires:
    - docker
    - gpu
    - af3_db_dir
    - af3_model_dir
```

Doctor and preflight should read this registry.

## Long Task Status

Use consistent task states:

```text
queued
running
feature_extraction
model_inference
postprocess
completed
failed
cancelled
```

## Artifact Types

| Type | Examples |
| --- | --- |
| structure | `.cif`, `.pdb` |
| table | `.csv`, `.tsv` |
| json | `.json` |
| report | `.md`, `.html`, `.pdf` |
| log | `stdout.log`, `stderr.log` |
| image | `.png`, `.svg` |

Every artifact must be registered in PostgreSQL before being shown or downloaded.

## AlphaFold 3

AF3 must always be queued:

```text
Agent tool call
-> create task
-> Celery worker
-> run AF3
-> write logs and output files
-> register artifacts
-> update task status
-> notify frontend
```

Never run AF3 synchronously in an API request.

## Doctor Checks

Doctor should check:

- LLM API.
- Embedding API.
- Qdrant.
- PostgreSQL.
- Redis.
- MinIO.
- model weights.
- Foldseek.
- DSSP.
- GPU.
- AF3 image/db/model dir.
- RCSB.
- UniProt.
- RNAcentral.
- MCP RNA expert.

