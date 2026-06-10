from dataclasses import dataclass


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
    ToolSpec("run_alphafold3", "Submit AlphaFold 3 structure prediction.", long_running=True),
    ToolSpec("read_result_file", "Read a registered artifact/result file."),
    ToolSpec("generate_session_report", "Generate a Markdown report for the current agent session."),
    ToolSpec(
        "remote_rna_expert__generate_rna_for_protein",
        "Generate RNA candidates for a protein chain through remote MCP.",
        long_running=True,
    ),
)


def list_tools() -> list[dict]:
    return [
        {"name": tool.name, "description": tool.description, "long_running": tool.long_running}
        for tool in TOOL_CATALOG
    ]


def openai_tool_schemas() -> list[dict]:
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
            "required": ["pdb_path", "ligand_type"],
            "properties": {
                "pdb_path": {"type": "string"},
                "ligand_type": {"type": "string", "enum": ["DNA", "RNA"]},
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
        "run_alphafold3": {
            "type": "object",
            "required": ["entities"],
            "properties": {
                "job_name": {"type": "string"},
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
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
        "remote_rna_expert__generate_rna_for_protein": {
            "type": "object",
            "required": ["pdb_id", "chain"],
            "properties": {
                "pdb_id": {"type": "string"},
                "chain": {"type": "string"},
                "num_samples": {"type": "integer"},
            },
        },
    }
    supported = set(schemas)
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": schemas[tool.name],
            },
        }
        for tool in TOOL_CATALOG
        if tool.name in supported
    ]
