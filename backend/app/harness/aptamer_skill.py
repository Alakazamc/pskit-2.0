"""AI4S 生信 ``aptamer_closed_loop`` Skill 的纯计划契约。

wzf：本模块只描述科研闭环、依赖、证据门和资源恢复语义，不连接数据库、
Worker、MCP、LLM 或真实科学工具，也不生成任何科研结果。运行时接线应把
这里的计划交给已验证的 Harness Executor，并以真实 Receipt/EvidenceGate 结果
更新状态。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


SKILL_ID = "aptamer_closed_loop"
SKILL_VERSION = "1.0.0-contract"
_LEGACY_FIELD = "research_run_id"


class StageStatus(str, Enum):
    PLANNED = "planned"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class StageSpec:
    stage_id: str
    label: str
    capabilities: tuple[str, ...]
    depends_on: tuple[str, ...] = ()
    parallel_group: str | None = None
    required_evidence: tuple[str, ...] = ()
    requires_gpu: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "label": self.label,
            "capabilities": list(self.capabilities),
            "depends_on": list(self.depends_on),
            "parallel_group": self.parallel_group,
            "required_evidence": list(self.required_evidence),
            "requires_gpu": self.requires_gpu,
        }


@dataclass(frozen=True, slots=True)
class StageState:
    stage_id: str
    status: StageStatus = StageStatus.PLANNED
    attempt: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "status": self.status.value,
            "attempt": self.attempt,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AptamerSkillManifest:
    skill_id: str
    version: str
    domain: str
    description: str
    ordinary_chat_research_run_optional: bool
    stages: tuple[StageSpec, ...]
    recovery: Mapping[str, Any]
    resource_policy: Mapping[str, Any]
    evidence_policy: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "version": self.version,
            "domain": self.domain,
            "description": self.description,
            "ordinary_chat_research_run_optional": self.ordinary_chat_research_run_optional,
            "stages": [stage.to_dict() for stage in self.stages],
            "recovery": dict(self.recovery),
            "resource_policy": dict(self.resource_policy),
            "evidence_policy": dict(self.evidence_policy),
        }


@dataclass(frozen=True, slots=True)
class AptamerExecutionPlan:
    skill_id: str
    skill_version: str
    session_id: str
    user_id: str
    target: Mapping[str, Any]
    stages: tuple[StageState, ...]

    def to_dict(self) -> dict[str, Any]:
        # Intentional contract: ordinary conversation/Skill execution has no
        # required or serialized research_run_id.
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "target": dict(self.target),
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass(frozen=True, slots=True)
class EvidenceGateDecision:
    allowed: bool
    stage_id: str
    missing: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "stage_id": self.stage_id,
            "missing": list(self.missing),
            "reason": self.reason,
        }


_STAGES = (
    StageSpec("target_analysis", "靶标分析", ("fetch_pdb_info", "download_pdb_file"), required_evidence=("target_metadata", "target_structure")),
    StageSpec("sequence_homology", "序列同源分析", ("search_sequence_homologs",), ("target_analysis",), "homology", ("sequence_homologs",)),
    StageSpec("structure_homology", "结构同源分析", ("search_structure_homologs",), ("target_analysis",), "homology", ("structure_homologs",)),
    StageSpec("binding_assessment", "结合评估", ("predict_binding_sites", "predict_interaction"), ("sequence_homology", "structure_homology"), required_evidence=("binding_assessment",)),
    StageSpec("coral_candidates", "CORAL RNA 候选", ("generate_coral_candidates",), ("binding_assessment",), "candidate_generation", ("coral_candidates",)),
    StageSpec("pepccd_candidates", "PepCCD 多肽候选", ("generate_pepccd_candidates",), ("binding_assessment",), "candidate_generation", ("pepccd_candidates",)),
    StageSpec("score_rna", "RNA 候选评分", ("score_research_candidates",), ("coral_candidates",), "candidate_evaluation", ("rna_scores",)),
    StageSpec("score_peptide", "多肽候选评分", ("score_research_candidates",), ("pepccd_candidates",), "candidate_evaluation", ("peptide_scores",)),
    StageSpec("top10_af3", "Top10 AlphaFold 3", ("submit_research_top10_af3", "run_alphafold3"), ("score_rna", "score_peptide"), requires_gpu=True, required_evidence=("af3_predictions",)),
    StageSpec("strategy_feedback", "策略反馈", ("record_strategy_feedback",), ("top10_af3",), required_evidence=("strategy_feedback",)),
    StageSpec("report", "科研报告", ("generate_research_report",), ("strategy_feedback",), required_evidence=("research_report",)),
)

_MANIFEST = AptamerSkillManifest(
    skill_id=SKILL_ID,
    version=SKILL_VERSION,
    domain="AI4S 生物信息学 / 蛋白靶向适配体设计",
    description="以可追溯证据驱动靶标分析、双轨候选生成、结构预测和策略反馈的科研 Skill。",
    ordinary_chat_research_run_optional=True,
    stages=_STAGES,
    recovery=MappingProxyType({
        "checkpointed": True,
        "retry_scope": "failed_stage_and_downstream_only",
        "preserve_succeeded_evidence": True,
        "idempotency": "reuse_task_graph_and_attempt_lease",
        "resource_resume": "resume_after_probe_without_replaying_completed_stages",
    }),
    resource_policy=MappingProxyType({
        "gpu_required_stages": ["top10_af3"],
        "no_gpu_status": StageStatus.WAITING_FOR_RESOURCE.value,
        "no_gpu_reason": "gpu_required_for_alphafold3",
        "never_fake_result": True,
    }),
    evidence_policy=MappingProxyType({
        "gate": "every_stage_receipt_must_be_terminal_and_registered",
        "required_receipt_fields": ["status", "evidence", "artifacts", "provenance", "hashes"],
        "artifact_requirements": ["registered", "content_hash"],
    }),
)


def get_aptamer_closed_loop_manifest() -> AptamerSkillManifest:
    return _MANIFEST


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _contains_legacy(value: Any) -> bool:
    if isinstance(value, Mapping):
        return _LEGACY_FIELD in value or any(_contains_legacy(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_legacy(item) for item in value)
    return False


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def build_aptamer_closed_loop_plan(*, session_id: str, user_id: str, target: Mapping[str, Any]) -> AptamerExecutionPlan:
    """构建闭环计划；纯函数，不执行任何能力。"""

    session_id = _require_text(session_id, "session_id")
    user_id = _require_text(user_id, "user_id")
    if not isinstance(target, Mapping) or not target:
        raise ValueError("target must be a non-empty mapping")
    if _contains_legacy(target):
        raise ValueError("aptamer_closed_loop target must not contain research_run_id")
    if not any(key in target for key in ("pdb_id", "protein_sequence", "structure_artifact_id")):
        raise ValueError("target requires pdb_id, protein_sequence, or structure_artifact_id")
    return AptamerExecutionPlan(
        SKILL_ID,
        SKILL_VERSION,
        session_id,
        user_id,
        _freeze_mapping(target),
        tuple(StageState(stage.stage_id) for stage in _STAGES),
    )


def _spec(stage_id: str) -> StageSpec:
    for stage in _STAGES:
        if stage.stage_id == stage_id:
            return stage
    raise ValueError(f"unknown stage: {stage_id}")


def ready_stages(plan: AptamerExecutionPlan) -> tuple[str, ...]:
    """返回当前依赖已满足、可并行提交的阶段 ID；不执行工具。"""

    state = {item.stage_id: item for item in plan.stages}
    result = []
    for stage in _STAGES:
        item = state[stage.stage_id]
        if item.status is not StageStatus.PLANNED:
            continue
        if all(state[dep].status is StageStatus.SUCCEEDED for dep in stage.depends_on):
            result.append(stage.stage_id)
    return tuple(result)


def evaluate_evidence_gate(stage_id: str, receipt: Mapping[str, Any]) -> EvidenceGateDecision:
    """对外部 Receipt 做纯证据门判断，不登记、不读取文件。"""

    stage = _spec(stage_id)
    if not isinstance(receipt, Mapping):
        return EvidenceGateDecision(False, stage_id, reason="receipt_not_mapping")
    missing: list[str] = []
    if receipt.get("status") != "succeeded":
        missing.append("status=succeeded")
    evidence = receipt.get("evidence")
    if not isinstance(evidence, (list, tuple, set, frozenset)):
        evidence = ()
    missing.extend(f"evidence:{name}" for name in stage.required_evidence if name not in evidence)
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, (list, tuple)) or not artifacts:
        missing.append("registered_artifact")
    else:
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, Mapping) or artifact.get("registered") is not True:
                missing.append(f"artifact[{index}].registered")
            if not isinstance(artifact, Mapping) or not artifact.get("content_hash"):
                missing.append(f"artifact[{index}].content_hash")
    if not receipt.get("provenance"):
        missing.append("provenance")
    if not receipt.get("hashes"):
        missing.append("hashes")
    return EvidenceGateDecision(not missing, stage_id, tuple(missing), "ok" if not missing else "evidence_gate_denied")


def apply_resource_gate(plan: AptamerExecutionPlan, resources: Mapping[str, Any]) -> AptamerExecutionPlan:
    """根据运行时资源探针标记 GPU 等待；无资源时绝不伪造 AF3 结果。"""

    if not isinstance(resources, Mapping):
        resources = {}
    raw_gpu = resources.get("gpu_count")
    try:
        gpu_count = int(raw_gpu) if raw_gpu is not None else 0
    except (TypeError, ValueError):
        gpu_count = 0
    states = {item.stage_id: item for item in plan.stages}
    af3 = states["top10_af3"]
    if af3.status in {StageStatus.PLANNED, StageStatus.WAITING_FOR_RESOURCE} and gpu_count < 1:
        states["top10_af3"] = replace(af3, status=StageStatus.WAITING_FOR_RESOURCE, reason="gpu_required_for_alphafold3")
        for downstream in ("strategy_feedback", "report"):
            item = states[downstream]
            if item.status is StageStatus.PLANNED:
                states[downstream] = replace(item, status=StageStatus.WAITING_FOR_DEPENDENCY, reason="top10_af3_waiting_for_resource")
    elif af3.status is StageStatus.WAITING_FOR_RESOURCE and gpu_count >= 1:
        states["top10_af3"] = replace(af3, status=StageStatus.PLANNED, reason="resource_available_resume")
        for downstream in ("strategy_feedback", "report"):
            item = states[downstream]
            if item.status is StageStatus.WAITING_FOR_DEPENDENCY and item.reason == "top10_af3_waiting_for_resource":
                states[downstream] = replace(item, status=StageStatus.PLANNED, reason="")
    return replace(plan, stages=tuple(states[item.stage_id] for item in _STAGES))


def recover_from_stage(plan: AptamerExecutionPlan, failed_stage_id: str) -> AptamerExecutionPlan:
    """失败恢复只重置失败节点及下游；已成功证据与尝试次数保持不变。"""

    _spec(failed_stage_id)
    downstream: set[str] = set()
    changed = True
    while changed:
        changed = False
        for stage in _STAGES:
            if stage.stage_id == failed_stage_id or any(dep in downstream or dep == failed_stage_id for dep in stage.depends_on):
                if stage.stage_id not in downstream:
                    downstream.add(stage.stage_id)
                    changed = True
    states = []
    for item in plan.stages:
        if item.stage_id not in downstream:
            states.append(item)
            continue
        next_status = StageStatus.PLANNED if item.stage_id == failed_stage_id else StageStatus.WAITING_FOR_DEPENDENCY
        states.append(replace(item, status=next_status, attempt=item.attempt + (1 if item.stage_id == failed_stage_id else 0), reason="recovery_checkpoint"))
    return replace(plan, stages=tuple(states))


__all__ = [
    "AptamerExecutionPlan",
    "AptamerSkillManifest",
    "EvidenceGateDecision",
    "SKILL_ID",
    "SKILL_VERSION",
    "StageSpec",
    "StageState",
    "StageStatus",
    "apply_resource_gate",
    "build_aptamer_closed_loop_plan",
    "evaluate_evidence_gate",
    "get_aptamer_closed_loop_manifest",
    "ready_stages",
    "recover_from_stage",
]
