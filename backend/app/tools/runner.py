from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Candidate, ResearchRun, Task, User
from app.research.context import (
    resolve_session_research_context,
    session_research_context_or_error,
)
from app.research.service import link_task_to_research_run
from app.tools.reports import (
    generate_harness_report,
    generate_research_report,
    generate_session_report,
)
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
    session_id: UUID | None
    tool_call_id: str
    operation_id: str | None = None
    agent_turn_id: UUID | None = None


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


def link_task_from_arguments(
    context: ToolContext,
    task: Task,
    args: dict,
    role: str,
) -> ResearchRun | None:
    """把异步任务归属到该会话的研究上下文，并返回所用上下文。"""

    reject_client_research_run_id(args)
    if context.session_id is None:
        return None
    research_run = resolve_session_research_context(
        context.db,
        context.user,
        context.session_id,
        create=True,
    )
    if research_run is None:
        return None
    candidate = None
    if args.get("candidate_id"):
        try:
            candidate_id = UUID(str(args["candidate_id"]))
        except ValueError as exc:
            raise ToolExecutionError("candidate_id must be a valid UUID") from exc
        candidate = context.db.get(Candidate, candidate_id)
        if (
            not candidate
            or candidate.user_id != context.user.id
            or candidate.research_run_id != research_run.id
        ):
            raise ToolExecutionError("Candidate not found")
    try:
        link_task_to_research_run(
            context.db,
            context.user,
            research_run,
            task,
            role,
            candidate,
        )
    except ValueError as exc:
        raise ToolExecutionError(str(exc)) from exc
    return research_run


def reject_client_research_run_id(args: dict) -> None:
    """ADR 0012：归属只能由服务端从会话解析，任意深度出现都拒绝。"""

    def contains_internal_owner(value) -> bool:
        if isinstance(value, dict):
            return "research_run_id" in value or any(
                contains_internal_owner(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_internal_owner(item) for item in value)
        return False

    if contains_internal_owner(args):
        raise ToolExecutionError(
            "research_run_id is resolved from the agent session and must not be supplied"
        )


def direct_af3_task_arguments(args: dict) -> dict:
    """Build the exact Worker payload for session-native AF3 calls."""

    entities = args.get("entities")
    if not isinstance(entities, list) or not entities:
        raise ToolExecutionError("run_alphafold3 requires a non-empty entities array")
    normalized_entities: list[dict[str, str]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            raise ToolExecutionError("run_alphafold3 entities must be objects")
        entity_type = entity.get("type")
        sequence = entity.get("sequence")
        if entity_type not in {"protein", "rna"}:
            raise ToolExecutionError("run_alphafold3 entity type must be protein or rna")
        if not isinstance(sequence, str) or not sequence.strip():
            raise ToolExecutionError("run_alphafold3 entity sequence must be non-empty")
        normalized_entities.append(
            {"type": str(entity_type), "sequence": sequence.strip()}
        )
    payload: dict = {"entities": normalized_entities}
    for key in ("job_name", "model_seed", "num_diffusion_samples"):
        if key in args:
            payload[key] = args[key]
    return payload


def owned_research_run(context: ToolContext, args: dict) -> ResearchRun:
    """返回该会话的研究上下文；必要时惰性建立。"""

    reject_client_research_run_id(args)
    return session_research_context_or_error(context.db, context.user, context.session_id)


def execute_tool(name: str, arguments: str | dict | None, context: ToolContext) -> dict:
    args = parse_tool_arguments(arguments)
    # 归属字段从不属于模型/客户端工具契约。入口统一拒绝，避免 direct
    # session-native 工具绕过后续 research-link helper 的校验。
    reject_client_research_run_id(args)
    if name == "predict_binding_sites":
        artifact_id = str(args.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ToolExecutionError(
                "predict_binding_sites requires a registered artifact_id; "
                "host pdb_path input is not accepted from Agent tool calls"
            )
        # wzf：Agent 侧只持久化登记产物 ID，避免把模型生成或旧会话中的
        # 宿主机路径继续带入异步 Worker。
        args = dict(args)
        args["artifact_id"] = artifact_id
        args.pop("pdb_path", None)
    if name == "score_research_candidates":
        from app.api.research import score_candidate_track
        from app.schemas.research import ScoreTrackRequest

        score_run = owned_research_run(context, args)
        track_value = str(args.get("track") or "")
        if track_value not in {"rna", "peptide"}:
            raise ToolExecutionError("track must be rna or peptide")
        score_payload = ScoreTrackRequest.model_validate(
            {
                "config_version": args.get("config_version"),
                "metrics": args.get("metrics"),
                "iteration": args.get("iteration"),
                "minimum_total_score": args.get("minimum_total_score", 0.0),
            }
        )
        score_response = score_candidate_track(
            score_run.id,
            cast(Literal["rna", "peptide"], track_value),
            score_payload,
            context.db,
            context.user,
        )
        return dict(score_response.model_dump(mode="json"))
    if name == "submit_research_top10_af3":
        from app.api.research import create_af3_batch
        from app.schemas.research import CreateAf3BatchRequest

        af3_run = owned_research_run(context, args)
        track_value = str(args.get("track") or "")
        if track_value not in {"rna", "peptide"}:
            raise ToolExecutionError("track must be rna or peptide")
        af3_payload = CreateAf3BatchRequest.model_validate(
            {
                "max_candidates": args.get("max_candidates", 10),
                "model_seed": args.get("model_seed", 42),
                "num_diffusion_samples": args.get("num_diffusion_samples", 5),
            }
        )
        af3_response = create_af3_batch(
            af3_run.id,
            cast(Literal["rna", "peptide"], track_value),
            af3_payload,
            context.db,
            context.user,
        )
        return dict(af3_response.model_dump(mode="json"))
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
    if name == "generate_harness_report":
        return generate_harness_report(
            context.db,
            context.user,
            context.session_id,
            title=args.get("title"),
        )
    if name == "generate_research_report":
        report_run = owned_research_run(context, args)
        return generate_research_report(
            context.db,
            context.user,
            report_run,
            operation_id=context.tool_call_id,
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
        "search_sequence_homologs",
        "search_structure_homologs",
        "generate_coral_candidates",
        "generate_pepccd_candidates",
        "run_alphafold3",
    }:
        role = {
            "search_sequence_homologs": "homology",
            "search_structure_homologs": "homology",
            "generate_coral_candidates": "candidate_generation",
            "generate_pepccd_candidates": "candidate_generation",
            "run_alphafold3": "af3",
            "predict_binding_sites": "binding",
            "extract_empirical_features": "binding",
        }.get(name, "candidate_evaluation")
        try:
            task_arguments = (
                direct_af3_task_arguments(args)
                if name == "run_alphafold3"
                else dict(args)
            )
            # 通用 run_alphafold3 是会话级任务：它使用模型提供的 entities，
            # 不创建或注入候选研究上下文。候选级 AF3 仍由
            # submit_research_top10_af3/create_af3_batch 负责完整 provenance 绑定。
            research_linked = name != "run_alphafold3"
            if research_linked and context.session_id is not None:
                session_context = resolve_session_research_context(
                    context.db,
                    context.user,
                    context.session_id,
                    create=True,
                )
                if session_context is not None:
                    # Worker/legacy persistence still needs an internal owner. Keep
                    # this server-generated field out of the model arguments and
                    # user-facing tool result.
                    task_arguments["research_run_id"] = str(session_context.id)
            task = create_queued_task(
                context.db,
                context.user,
                name,
                task_arguments,
                session_id=context.session_id,
                tool_call_id=context.tool_call_id,
                operation_id=context.operation_id,
                commit=False,
            )
            if research_linked:
                link_task_from_arguments(context, task, args, role)
            context.db.commit()
            context.db.refresh(task)
        except Exception:
            context.db.rollback()
            raise
        return task_to_result(task)
    if name == "predict_interaction":
        normalized = dict(args)
        if "protein_seq" not in normalized and "protein_sequence" in normalized:
            normalized["protein_seq"] = normalized["protein_sequence"]
        if "nucleic_acid_seq" not in normalized and "nucleic_sequence" in normalized:
            normalized["nucleic_acid_seq"] = normalized["nucleic_sequence"]
        try:
            task = create_queued_task(
                context.db,
                context.user,
                name,
                normalized,
                session_id=context.session_id,
                tool_call_id=context.tool_call_id,
                operation_id=context.operation_id,
                commit=False,
            )
            link_task_from_arguments(context, task, normalized, "binding")
            context.db.commit()
            context.db.refresh(task)
        except Exception:
            context.db.rollback()
            raise
        return task_to_result(task)
    raise ToolExecutionError(f"Tool is not implemented yet in PSKit 2.0: {name}")


def execute_tool_as_text(name: str, arguments: str | dict | None, context: ToolContext) -> str:
    return json_dumps_result(execute_tool(name, arguments, context))
