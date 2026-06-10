from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import User
from app.tools.reports import generate_session_report
from app.tools.external import (
    ToolExecutionError,
    download_pdb_file,
    fetch_pdb_info,
    fetch_rnacentral_entry,
    fetch_uniprot_entry,
    json_dumps_result,
    read_result_file,
    search_pdb,
    search_rnacentral,
    search_uniprot,
    serpapi_search,
)
from app.tools.structure import (
    annotate_binding_pairs,
    calculate_contact_map,
    extract_fragment,
    split_complex,
    split_pdb_by_chain,
)
from app.tasks.service import create_queued_task, task_to_result


@dataclass
class ToolContext:
    db: Session
    user: User
    session_id: UUID
    tool_call_id: str


def parse_tool_arguments(raw: str | dict | None) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ToolExecutionError(f"Invalid tool JSON arguments: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ToolExecutionError("Tool arguments must be a JSON object")
    return parsed


def execute_tool(name: str, arguments: str | dict | None, context: ToolContext) -> dict:
    args = parse_tool_arguments(arguments)
    if name == "search_pdb":
        return search_pdb(query=args["query"])
    if name == "fetch_pdb_info":
        return fetch_pdb_info(pdb_id=args["pdb_id"])
    if name == "download_pdb_file":
        return download_pdb_file(
            context.db,
            context.user,
            context.session_id,
            context.tool_call_id,
            pdb_id=args["pdb_id"],
            file_format=args.get("format", "cif"),
        )
    if name == "search_uniprot":
        return search_uniprot(query=args["query"])
    if name == "fetch_uniprot_entry":
        return fetch_uniprot_entry(accession=args["accession"])
    if name == "search_rnacentral":
        return search_rnacentral(query=args["query"])
    if name == "fetch_rnacentral_entry":
        return fetch_rnacentral_entry(accession=args["accession"])
    if name == "serpapi_search":
        return serpapi_search(
            query=args["query"],
            num=int(args.get("num", 5)),
            engine=args.get("engine", "google"),
        )
    if name == "read_result_file":
        return read_result_file(
            context.db,
            context.user,
            artifact_id=args.get("artifact_id"),
            file_path=args.get("file_path"),
            max_chars=int(args.get("max_chars", 8000)),
        )
    if name == "generate_session_report":
        return generate_session_report(
            context.db,
            context.user,
            context.session_id,
            context.tool_call_id,
            title=args.get("title"),
        )
    if name == "split_pdb_by_chain":
        return split_pdb_by_chain(
            context.db,
            context.user,
            context.session_id,
            context.tool_call_id,
            pdb_path=args["pdb_path"],
            file_format=args["format"],
        )
    if name == "split_complex":
        return split_complex(
            context.db,
            context.user,
            context.session_id,
            context.tool_call_id,
            pdb_path=args["pdb_path"],
            file_format=args["format"],
        )
    if name == "extract_fragment":
        return extract_fragment(
            context.db,
            context.user,
            context.session_id,
            context.tool_call_id,
            pdb_path=args["pdb_path"],
            chain=args["chain"],
            file_format=args["format"],
            start=args.get("start"),
            end=args.get("end"),
        )
    if name == "calculate_contact_map":
        return calculate_contact_map(
            context.db,
            context.user,
            context.session_id,
            context.tool_call_id,
            pdb_path=args["pdb_path"],
            file_format=args["format"],
            chain=args.get("chain"),
            mode=args.get("mode", "d"),
            k=args.get("k"),
        )
    if name == "annotate_binding_pairs":
        return annotate_binding_pairs(
            context.db,
            context.user,
            context.session_id,
            context.tool_call_id,
            pdb_path=args["pdb_path"],
            file_format=args["format"],
            cutoff=float(args.get("cutoff", 3.5)),
        )
    if name in {
        "predict_binding_sites",
        "extract_empirical_features",
        "run_alphafold3",
        "remote_rna_expert__generate_rna_for_protein",
    }:
        task = create_queued_task(
            context.db,
            context.user,
            name,
            args,
            session_id=context.session_id,
            tool_call_id=context.tool_call_id,
        )
        return task_to_result(task)
    if name == "predict_interaction":
        normalized = dict(args)
        if "protein_seq" not in normalized and "protein_sequence" in normalized:
            normalized["protein_seq"] = normalized["protein_sequence"]
        if "nucleic_acid_seq" not in normalized and "nucleic_sequence" in normalized:
            normalized["nucleic_acid_seq"] = normalized["nucleic_sequence"]
        task = create_queued_task(
            context.db,
            context.user,
            name,
            normalized,
            session_id=context.session_id,
            tool_call_id=context.tool_call_id,
        )
        return task_to_result(task)
    raise ToolExecutionError(f"Tool is not implemented yet in PSKit 2.0: {name}")


def execute_tool_as_text(name: str, arguments: str | dict | None, context: ToolContext) -> str:
    return json_dumps_result(execute_tool(name, arguments, context))
