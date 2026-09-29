from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    long_running: bool = False


TOOL_CATALOG: tuple[ToolSpec, ...] = (
    ToolSpec("search_pdb", "Search RCSB PDB entries by natural-language query."),
    ToolSpec("serpapi_search", "Search the public web through SerpAPI."),
    ToolSpec("download_pdb_file", "Download a PDB/mmCIF structure file."),
    ToolSpec("search_uniprot", "Search UniProt entries."),
    ToolSpec("fetch_uniprot_entry", "Fetch UniProt metadata."),
    ToolSpec("search_rnacentral", "Search RNAcentral entries."),
    ToolSpec("fetch_rnacentral_entry", "Fetch RNAcentral metadata."),
    ToolSpec("fetch_pdb_info", "Fetch RCSB PDB metadata."),
    ToolSpec("split_pdb_by_chain", "Split a structure by chain."),
    ToolSpec("split_complex", "Split protein and nucleic-acid parts."),
    ToolSpec("extract_fragment", "Extract a chain fragment."),
    ToolSpec("calculate_contact_map", "Calculate a structural contact map."),
    ToolSpec("annotate_binding_pairs", "Annotate protein-nucleic-acid residue contacts."),
    ToolSpec("predict_binding_sites", "Predict DNA/RNA binding sites.", long_running=True),
    ToolSpec("extract_empirical_features", "Extract DSSP/Rosetta-like features.", long_running=True),
    ToolSpec("predict_interaction", "Predict sequence-level protein-nucleic interaction.", long_running=True),
    ToolSpec("search_sequence_homologs", "Search sequence homologs with MMseqs2 or BLASTP.", long_running=True),
    ToolSpec("search_structure_homologs", "Search structure homologs with Foldseek.", long_running=True),
    ToolSpec(
        "generate_coral_candidates",
        "Generate and persist CORAL RNA aptamer candidates through the remote RNA MCP service.",
        long_running=True,
    ),
    ToolSpec(
        "generate_pepccd_candidates",
        "Generate and persist peptide aptamer candidates through the PepCCD MCP service.",
        long_running=True,
    ),
    ToolSpec("score_research_candidates", "Score and rank one candidate track with an explicit configuration."),
    ToolSpec(
        "submit_research_top10_af3",
        "Submit up to ten ranked candidates from one track to AF3.",
        long_running=True,
    ),
    ToolSpec("run_alphafold3", "Submit AlphaFold 3 structure prediction.", long_running=True),
    ToolSpec(
        "list_task_artifacts",
        "List the status and registered artifact IDs for an owned task, including older tasks.",
    ),
    ToolSpec("read_result_file", "Read a registered artifact/result file."),
    ToolSpec("generate_session_report", "Generate a Markdown report for the current agent session."),
    ToolSpec("generate_harness_report", "生成基于持久化 Harness 证据的只读报告。"),
    ToolSpec("generate_research_report", "Generate a traceable Markdown report for a research run."),
)


def list_tools() -> list[dict]:
    return [
        {"name": tool.name, "description": tool.description, "long_running": tool.long_running}
        for tool in TOOL_CATALOG
    ]


def openai_tool_schemas(
    allowed_names: set[str] | None = None,
    preferred_order: Iterable[str] | None = None,
) -> list[dict]:
    schemas: dict[str, dict] = {
        "search_pdb": {
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        "download_pdb_file": {
            "type": "object",
            "required": ["pdb_id"],
            "properties": {
                "pdb_id": {"type": "string"},
                "format": {"type": "string", "enum": ["cif", "pdb"]},
            },
        },
        "fetch_pdb_info": {
            "type": "object",
            "required": ["pdb_id"],
            "properties": {"pdb_id": {"type": "string"}},
        },
        "search_uniprot": {
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        "fetch_uniprot_entry": {
            "type": "object",
            "required": ["accession"],
            "properties": {"accession": {"type": "string"}},
        },
        "search_rnacentral": {
            "type": "object",
            "required": ["query"],
            "properties": {"query": {"type": "string"}},
        },
        "fetch_rnacentral_entry": {
            "type": "object",
            "required": ["accession"],
            "properties": {"accession": {"type": "string"}},
        },
        "serpapi_search": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "num": {"type": "integer"},
                "engine": {"type": "string"},
            },
        },
        "list_task_artifacts": {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Exact task UUID shown by a previous tool call or the task list.",
                },
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 8},
            },
        },
        "read_result_file": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "file_path": {"type": "string"},
                "max_chars": {"type": "integer"},
            },
        },
        "generate_session_report": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
            },
        },
        "generate_harness_report": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
            },
        },
        "generate_research_report": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
            },
        },
        "split_pdb_by_chain": {
            "type": "object",
            "required": ["pdb_path", "format"],
            "properties": {
                "pdb_path": {"type": "string"},
                "format": {"type": "string", "enum": ["cif", "pdb"]},
            },
        },
        "split_complex": {
            "type": "object",
            "required": ["pdb_path", "format"],
            "properties": {
                "pdb_path": {"type": "string"},
                "format": {"type": "string", "enum": ["cif", "pdb"]},
            },
        },
        "extract_fragment": {
            "type": "object",
            "required": ["pdb_path", "chain", "format"],
            "properties": {
                "pdb_path": {"type": "string"},
                "chain": {"type": "string"},
                "format": {"type": "string", "enum": ["cif", "pdb"]},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
        },
        "calculate_contact_map": {
            "type": "object",
            "required": ["pdb_path", "format"],
            "properties": {
                "pdb_path": {"type": "string"},
                "format": {"type": "string", "enum": ["cif", "pdb"]},
                "chain": {"type": "string"},
                "mode": {"type": "string", "enum": ["d", "knn"]},
                "k": {"type": "integer"},
            },
        },
        "annotate_binding_pairs": {
            "type": "object",
            "required": ["pdb_path", "format"],
            "properties": {
                "pdb_path": {"type": "string"},
                "format": {"type": "string", "enum": ["cif", "pdb"]},
                "cutoff": {"type": "number"},
            },
        },
        "predict_binding_sites": {
            "type": "object",
            "required": ["artifact_id", "ligand_type"],
            "properties": {
                "artifact_id": {
                    "type": "string",
                    "description": (
                        "Preferred registered structure artifact UUID returned by "
                        "download_pdb_file or another structure tool."
                    ),
                },
                "ligand_type": {"type": "string", "enum": ["DNA", "RNA"]},
            },
        },
        "extract_empirical_features": {
            "type": "object",
            "anyOf": [
                {"required": ["artifact_id"], "properties": {"artifact_id": {"type": "string"}}},
                {"required": ["pdb_path"], "properties": {"pdb_path": {"type": "string"}}},
            ],
            "properties": {
                "pdb_path": {"type": "string"},
                "artifact_id": {
                    "type": "string",
                    "description": "Preferred registered structure artifact UUID; use without pdb_path.",
                },
                "emp_feats": {"type": "string"},
                "rosetta_relax": {"type": "boolean"},
            },
        },
        "predict_interaction": {
            "type": "object",
            "required": ["protein_sequence", "nucleic_sequence"],
            "properties": {
                "protein_sequence": {"type": "string"},
                "nucleic_sequence": {"type": "string"},
            },
        },
        "search_sequence_homologs": {
            "type": "object",
            "required": ["protein_sequence"],
            "properties": {
                "protein_sequence": {"type": "string"},
                "max_hits": {"type": "integer", "minimum": 1, "maximum": 500},
                "evalue": {"type": "number"},
                "sensitivity": {"type": "number"},
            },
        },
        "search_structure_homologs": {
            "type": "object",
            "anyOf": [
                {"required": ["artifact_id"], "properties": {"artifact_id": {"type": "string"}}},
                {"required": ["pdb_path"], "properties": {"pdb_path": {"type": "string"}}},
            ],
            "properties": {
                "pdb_path": {"type": "string"},
                "artifact_id": {
                    "type": "string",
                    "description": "Preferred registered structure artifact UUID; use without pdb_path.",
                },
                "chain": {"type": "string"},
                "max_hits": {"type": "integer", "minimum": 1, "maximum": 500},
                "evalue": {"type": "number"},
                "sensitivity": {"type": "number"},
            },
        },
        "generate_coral_candidates": {
            "type": "object",
            "required": ["pdb_id", "chain"],
            "properties": {
                "pdb_id": {"type": "string"},
                "chain": {"type": "string"},
                "num_candidates": {"type": "integer", "minimum": 1},
                "num_samples": {"type": "integer", "minimum": 1},
                "length": {"type": "integer", "minimum": 1},
                "iteration": {"type": "integer", "minimum": 0},
            },
        },
        "generate_pepccd_candidates": {
            "type": "object",
            "required": ["protein_sequence"],
            "properties": {
                "protein_sequence": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4096,
                },
                "num_peptides": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "default": 10,
                },
                "peptide_length": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 40,
                    "default": 15,
                },
                "sample_batch_size": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                },
                "temperature": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 5,
                    "default": 1.0,
                },
                "seed": {
                    "anyOf": [
                        {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 4294967295,
                        },
                        {"type": "null"},
                    ],
                    "default": None,
                },
                "device": {
                    "type": "string",
                    "enum": ["cuda:0", "cuda:1", "cpu"],
                    "default": "cuda:0",
                },
                "iteration": {"type": "integer", "minimum": 0},
            },
        },
        "score_research_candidates": {
            "type": "object",
            "required": ["track", "config_version", "metrics"],
            "properties": {
                "track": {"type": "string", "enum": ["rna", "peptide"]},
                "config_version": {"type": "string"},
                "iteration": {"type": "integer", "minimum": 0},
                "minimum_total_score": {"type": "number", "minimum": 0, "maximum": 1},
                "metrics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name", "weight"],
                        "properties": {
                            "name": {"type": "string"},
                            "weight": {"type": "number", "exclusiveMinimum": 0},
                            "direction": {"type": "string", "enum": ["higher", "lower"]},
                            "missing_policy": {"type": "string", "enum": ["reject", "zero", "ignore"]}
                        }
                    }
                }
            }
        },
        "submit_research_top10_af3": {
            "type": "object",
            "required": ["track"],
            "properties": {
                "track": {"type": "string", "enum": ["rna", "peptide"]},
                "max_candidates": {"type": "integer", "minimum": 1, "maximum": 10},
                "model_seed": {"type": "integer"},
                "num_diffusion_samples": {"type": "integer", "minimum": 1}
            }
        },
        "run_alphafold3": {
            "type": "object",
            "required": ["entities"],
            "properties": {
                "job_name": {"type": "string"},
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["type", "sequence"],
                        "properties": {
                            "type": {"type": "string", "enum": ["protein", "rna"]},
                            "sequence": {"type": "string"},
                        },
                    },
                },
                "model_seed": {"type": "integer"},
                "num_diffusion_samples": {"type": "integer"},
            },
        },
    }
    # 工具参数对象统一封闭：模型不能通过未声明字段走到 Worker payload。
    # 运行时仍由 execute_tool 做同一归属边界的强制校验。
    for schema in schemas.values():
        schema["additionalProperties"] = False
    supported = set(schemas)
    result = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": schemas[tool.name],
            },
        }
        for tool in TOOL_CATALOG
        if tool.name in supported and (allowed_names is None or tool.name in allowed_names)
    ]
    order = {name: index for index, name in enumerate(preferred_order or [])}
    if order:
        result.sort(
            key=lambda item: (
                order.get(item["function"]["name"], len(order)),
                next(
                    index
                    for index, tool in enumerate(TOOL_CATALOG)
                    if tool.name == item["function"]["name"]
                ),
            )
        )
    return result


def list_capability_manifests() -> list[object]:
    """返回 AI4S Harness 能力清单；旧工具目录行为保持不变。

    采用函数内导入避免 ``capabilities`` 为覆盖 Catalog 而形成循环依赖。该
    入口只提供新 Manifest 的兼容查询，不改变 ``list_tools`` 或
    ``openai_tool_schemas`` 的返回值和旧运行时输入契约。
    """

    from app.harness.capabilities import list_capability_manifests as _list_manifests

    return _list_manifests()


def get_capability_manifest(capability_id: str) -> object:
    """按能力 ID 查询新 Harness Manifest。"""

    from app.harness.capabilities import get_capability_manifest as _get_manifest

    return _get_manifest(capability_id)
