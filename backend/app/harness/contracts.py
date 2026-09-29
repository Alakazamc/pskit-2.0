"""AI4S Harness 能力调用的不可变值对象。

这些类型只描述调用边界、资源请求、权限判定和证据/完成语义；它们不连接
数据库，也不执行任何工具。具体能力由 ``capabilities.py`` 登记，执行器可在
后续迁移中把这些值对象映射到任务和执行尝试。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from math import isfinite
import re
from types import MappingProxyType
from typing import Any


class CapabilityResultStatus(str, Enum):
    """能力调用的可审计状态，不把计划或模型文本当作成功。"""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    WAITING_FOR_INPUT = "waiting_for_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"


# 便于调用方使用更短的领域别名，同时保持单一状态枚举。
CapabilityStatus = CapabilityResultStatus
ExecutionStatus = CapabilityResultStatus


_TERMINAL_STATUSES = frozenset(
    {
        CapabilityResultStatus.SUCCEEDED,
        CapabilityResultStatus.FAILED,
        CapabilityResultStatus.TIMED_OUT,
        CapabilityResultStatus.CANCELLED,
        CapabilityResultStatus.INTERRUPTED,
        CapabilityResultStatus.BLOCKED,
    }
)


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_bounded_text(value: object, field_name: str, *, maximum: int) -> str:
    text = _require_text(value, field_name)
    if len(text) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return text


def _freeze_value(value: Any) -> Any:
    """递归冻结调用输入和元数据，避免值对象被调用方原地改写。"""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    """把内部不可变值还原为 JSON 可编码的普通容器。"""

    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_thaw_value(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value


def _json_ready(value: Any) -> Any:
    """把收据边界限制为确定性的 JSON 值。

    普通 Harness 输入沿用 ``_freeze_value`` 的兼容语义；收据是外部执行器
    交回的真实性边界，不能接受不可序列化对象、非字符串键或 NaN。
    """

    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise TypeError("receipt metadata keys must be non-empty strings")
            result[key] = _json_ready(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, frozenset)):
        raise TypeError("receipt metadata must not contain sets")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("receipt datetime must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Enum):
        return _json_ready(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("receipt metadata must not contain non-finite numbers")
        return value
    raise TypeError(f"receipt metadata contains non-JSON value: {type(value).__name__}")


def _freeze_json(value: Any) -> Any:
    """校验并递归冻结一个收据中的 JSON 值。"""

    ready = _json_ready(value)
    return _freeze_value(ready)


def _thaw_json(value: Any) -> Any:
    return _thaw_value(value)


def canonical_json_hash(value: Any) -> str:
    """返回统一的 ``sha256:<hex>`` 内容寻址摘要。"""

    payload = json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


# 简短别名供 Worker/Verifier 使用；它们都指向同一确定性算法。
canonical_hash = canonical_json_hash


_HASH_PATTERN = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")


def _require_hash(value: object, field_name: str) -> str:
    text = _require_bounded_text(value, field_name, maximum=80)
    if _HASH_PATTERN.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a sha256 digest")
    return text.lower()


def _coerce_datetime(value: object, field_name: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime or ISO-8601 string")
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _freeze_texts(values: Sequence[str] | None, field_name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raise ValueError(f"{field_name} must be a sequence of strings")
    result = tuple(_require_text(item, field_name) for item in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{field_name} must not contain duplicates")
    return result


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    """一次权限策略判定。

    ``granted`` 默认是 ``False``，因此缺少策略结果时天然 fail-closed。能力
    Manifest 只声明所需权限，不会因为登记能力而自动授予这些权限。
    """

    permission: str
    granted: bool = False
    reason: str = ""
    approval_required: bool = False
    policy_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "permission", _require_text(self.permission, "permission"))
        if not isinstance(self.granted, bool):
            raise TypeError("granted must be a bool")
        if not isinstance(self.approval_required, bool):
            raise TypeError("approval_required must be a bool")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string")
        if self.policy_id is not None:
            object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        if (self.granted or self.approval_required) and self.policy_id is None:
            raise ValueError("granted/approval-required decision must identify policy_id")

    @property
    def allowed(self) -> bool:
        """兼容策略层常用命名。"""

        return self.granted and not self.approval_required

    @property
    def requires_approval(self) -> bool:
        return self.approval_required

    @property
    def decision(self) -> str:
        return (
            "allow" if self.allowed else ("approval_required" if self.approval_required else "deny")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "permission": self.permission,
            "granted": self.granted,
            "allowed": self.allowed,
            "reason": self.reason,
            "approval_required": self.approval_required,
            "policy_id": self.policy_id,
            "decision": self.decision,
        }


@dataclass(frozen=True, slots=True)
class ResourceRequest:
    """一次科学执行尝试的资源需求，不代表调度器已经授予资源。"""

    cpu_cores: float = 0.0
    memory_mb: int = 0
    gpu_count: int = 0
    gpu_memory_mb: int = 0
    disk_mb: int = 0
    worker_labels: tuple[str, ...] = ()
    external_dependencies: tuple[str, ...] = ()
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.cpu_cores, bool) or not isinstance(self.cpu_cores, (int, float)):
            raise TypeError("cpu_cores must be a number")
        cpu_cores = float(self.cpu_cores)
        if not isfinite(cpu_cores) or cpu_cores < 0:
            raise ValueError("cpu_cores must be finite and non-negative")
        object.__setattr__(self, "cpu_cores", cpu_cores)
        for field_name in ("memory_mb", "gpu_count", "gpu_memory_mb", "disk_mb"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.timeout_seconds is not None:
            if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, int):
                raise TypeError("timeout_seconds must be an integer or None")
            if self.timeout_seconds <= 0:
                raise ValueError("timeout_seconds must be positive when provided")
        object.__setattr__(
            self, "worker_labels", _freeze_texts(self.worker_labels, "worker_labels")
        )
        object.__setattr__(
            self,
            "external_dependencies",
            _freeze_texts(self.external_dependencies, "external_dependencies"),
        )

    @property
    def requires_gpu(self) -> bool:
        return self.gpu_count > 0 or self.gpu_memory_mb > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cpu_cores": self.cpu_cores,
            "memory_mb": self.memory_mb,
            "gpu_count": self.gpu_count,
            "gpu_memory_mb": self.gpu_memory_mb,
            "disk_mb": self.disk_mb,
            "worker_labels": list(self.worker_labels),
            "external_dependencies": list(self.external_dependencies),
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True, slots=True)
class CompletionContract:
    """判断一次能力尝试是否完成的最小、可观察条件。"""

    required_output_fields: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    minimum_artifact_count: int = 0
    accepted_statuses: tuple[str, ...] = (CapabilityResultStatus.SUCCEEDED.value,)
    requires_terminal_status: bool = True
    requires_structured_output: bool = True
    allow_partial: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_output_fields",
            _freeze_texts(self.required_output_fields, "required_output_fields"),
        )
        object.__setattr__(
            self,
            "required_artifacts",
            _freeze_texts(self.required_artifacts, "required_artifacts"),
        )
        statuses = _freeze_texts(self.accepted_statuses, "accepted_statuses")
        unknown = set(statuses) - {status.value for status in CapabilityResultStatus}
        if unknown:
            raise ValueError(f"accepted_statuses contains unknown values: {sorted(unknown)}")
        if not statuses:
            raise ValueError("accepted_statuses must not be empty")
        object.__setattr__(self, "accepted_statuses", statuses)
        if isinstance(self.minimum_artifact_count, bool) or not isinstance(
            self.minimum_artifact_count, int
        ):
            raise TypeError("minimum_artifact_count must be an integer")
        if self.minimum_artifact_count < 0:
            raise ValueError("minimum_artifact_count must be non-negative")
        for field_name in (
            "requires_terminal_status",
            "requires_structured_output",
            "allow_partial",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a bool")

    @property
    def success_statuses(self) -> tuple[str, ...]:
        return self.accepted_statuses

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_output_fields": list(self.required_output_fields),
            "required_artifacts": list(self.required_artifacts),
            "minimum_artifact_count": self.minimum_artifact_count,
            "accepted_statuses": list(self.accepted_statuses),
            "requires_terminal_status": self.requires_terminal_status,
            "requires_structured_output": self.requires_structured_output,
            "allow_partial": self.allow_partial,
        }


@dataclass(frozen=True, slots=True)
class EvidenceContract:
    """能力结果必须携带的来源、产物和哈希证据要求。"""

    required_evidence_types: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    minimum_evidence_count: int = 0
    minimum_artifact_count: int = 0
    require_provenance: bool = True
    require_hash: bool = True
    reviewer_required: bool = False
    claims_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_evidence_types",
            _freeze_texts(self.required_evidence_types, "required_evidence_types"),
        )
        object.__setattr__(
            self,
            "required_artifacts",
            _freeze_texts(self.required_artifacts, "required_artifacts"),
        )
        object.__setattr__(self, "claims_scope", _freeze_texts(self.claims_scope, "claims_scope"))
        for field_name in ("minimum_evidence_count", "minimum_artifact_count"):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{field_name} must be an integer")
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        for field_name in ("require_provenance", "require_hash", "reviewer_required"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"{field_name} must be a bool")

    @property
    def required_evidence(self) -> tuple[str, ...]:
        return self.required_evidence_types

    def to_dict(self) -> dict[str, Any]:
        return {
            "required_evidence_types": list(self.required_evidence_types),
            "required_artifacts": list(self.required_artifacts),
            "minimum_evidence_count": self.minimum_evidence_count,
            "minimum_artifact_count": self.minimum_artifact_count,
            "require_provenance": self.require_provenance,
            "require_hash": self.require_hash,
            "reviewer_required": self.reviewer_required,
            "claims_scope": list(self.claims_scope),
        }


@dataclass(frozen=True, slots=True)
class CapabilityInvocationRequest:
    """Session 原生调用请求。

    wzf：会话、用户和幂等键属于服务端执行边界，必须由可信应用层提供；权限
    判定和资源授予不接收调用方自报值，避免请求对象变成自我授权通道。
    """

    capability_id: str
    session_id: str
    user_id: str
    idempotency_key: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    turn_id: str | None = None
    goal_id: str | None = None
    skill_execution_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "capability_id",
            _require_bounded_text(self.capability_id, "capability_id", maximum=160),
        )
        for field_name in ("session_id", "user_id", "idempotency_key"):
            maximum = 240 if field_name == "idempotency_key" else 300
            object.__setattr__(
                self,
                field_name,
                _require_bounded_text(getattr(self, field_name), field_name, maximum=maximum),
            )
        if not isinstance(self.inputs, Mapping):
            raise TypeError("inputs must be a mapping")
        object.__setattr__(self, "inputs", _freeze_value(self.inputs))
        for field_name in ("turn_id", "goal_id", "skill_execution_id"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _require_text(value, field_name))

    @property
    def has_legacy_research_run_input(self) -> bool:
        """仅用于审计/兼容诊断，不把旧字段变成请求类的必填字段。"""

        return "research_run_id" in self.inputs

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "inputs": _thaw_value(self.inputs),
            "session_id": self.session_id,
            "user_id": self.user_id,
            "turn_id": self.turn_id,
            "goal_id": self.goal_id,
            "skill_execution_id": self.skill_execution_id,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    """Worker 返回的已登记产物摘要。

    首次同步的 ``fetch_pdb_info`` 可以没有产物；一旦能力契约要求产物，
    ReceiptEvidenceGate 会同时要求 ``registered``、摘要和内容哈希成立。
    """

    artifact_id: str
    content_hash: str
    summary: Mapping[str, Any] = field(default_factory=dict)
    registered: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "artifact_id", _require_bounded_text(self.artifact_id, "artifact_id", maximum=300)
        )
        object.__setattr__(self, "content_hash", _require_hash(self.content_hash, "content_hash"))
        if not isinstance(self.summary, Mapping):
            raise TypeError("artifact summary must be a mapping")
        object.__setattr__(self, "summary", _freeze_json(self.summary))
        if not isinstance(self.registered, bool):
            raise TypeError("artifact registered must be a bool")

    @property
    def sha256(self) -> str:
        return self.content_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "content_hash": self.content_hash,
            "summary": _thaw_json(self.summary),
            "registered": self.registered,
        }


@dataclass(frozen=True, slots=True)
class EvidenceDraft:
    """尚未落库、但由受信 Worker 收据绑定的 Evidence 草稿。"""

    evidence_type: str
    statement: str
    artifact_ids: tuple[str, ...] = ()
    source_uri: str | None = None
    content_hash: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    reviewer_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_type",
            _require_bounded_text(self.evidence_type, "evidence_type", maximum=160),
        )
        object.__setattr__(
            self, "statement", _require_bounded_text(self.statement, "statement", maximum=4000)
        )
        object.__setattr__(self, "content_hash", _require_hash(self.content_hash, "content_hash"))
        ids = _freeze_texts(self.artifact_ids, "artifact_ids")
        object.__setattr__(self, "artifact_ids", ids)
        if self.source_uri is not None:
            object.__setattr__(
                self,
                "source_uri",
                _require_bounded_text(self.source_uri, "source_uri", maximum=2000),
            )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("evidence metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))
        if self.reviewer_id is not None:
            object.__setattr__(
                self,
                "reviewer_id",
                _require_bounded_text(self.reviewer_id, "reviewer_id", maximum=300),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_type": self.evidence_type,
            "statement": self.statement,
            "artifact_ids": list(self.artifact_ids),
            "source_uri": self.source_uri,
            "content_hash": self.content_hash,
            "metadata": _thaw_json(self.metadata),
            "reviewer_id": self.reviewer_id,
        }


@dataclass(frozen=True, slots=True)
class ProvenanceDraft:
    """尚未落库、但由受信 Worker 收据绑定的 Provenance 草稿。"""

    source_type: str
    source_id: str
    relation_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("source_type", "source_id", "relation_type"):
            object.__setattr__(
                self,
                field_name,
                _require_bounded_text(getattr(self, field_name), field_name, maximum=300),
            )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("provenance metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_json(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source_id": self.source_id,
            "relation_type": self.relation_type,
            "metadata": _thaw_json(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    """一次同步能力执行的受信收据。

    Receipt 只携带证明材料和摘要，不保存密钥。``authenticity_proof`` 由注入的
    ReceiptVerifier 消费；默认 ``to_dict`` 刻意省略它，避免结果摘要泄露证明材料。
    """

    receipt_id: str
    issuer: str
    user_id: str
    session_id: str
    invocation_id: str
    task_id: str
    attempt_id: str
    capability_id: str
    capability_version: str
    manifest_digest: str
    input_hash: str
    output_hash: str
    result_status: str | CapabilityResultStatus
    issued_at: datetime | str
    evidence: tuple[EvidenceDraft, ...] = ()
    provenance: tuple[ProvenanceDraft, ...] = ()
    artifacts: tuple[ArtifactReceipt, ...] = ()
    authenticity_proof: str | None = None
    # 以下别名只用于接收不同 Worker 适配器的明确命名，初始化后不保留第二份状态。
    evidence_drafts: tuple[EvidenceDraft, ...] | None = None
    provenance_drafts: tuple[ProvenanceDraft, ...] | None = None
    artifact_summaries: tuple[ArtifactReceipt, ...] | None = None
    artifact_drafts: tuple[ArtifactReceipt, ...] | None = None
    scientific_task_id: str | None = None
    signature: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "receipt_id",
            "issuer",
            "user_id",
            "session_id",
            "invocation_id",
            "task_id",
            "attempt_id",
            "capability_id",
            "capability_version",
            "manifest_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_bounded_text(getattr(self, field_name), field_name, maximum=400),
            )
        if self.scientific_task_id is not None:
            if self.task_id != self.scientific_task_id:
                raise ValueError("task_id and scientific_task_id disagree")
        object.__setattr__(self, "issued_at", _coerce_datetime(self.issued_at, "issued_at"))
        status = self.result_status
        if isinstance(status, CapabilityResultStatus):
            status = status.value
        status = _require_bounded_text(status, "result_status", maximum=80)
        if status not in {item.value for item in CapabilityResultStatus}:
            raise ValueError(f"unknown receipt result_status: {status}")
        object.__setattr__(self, "result_status", status)
        object.__setattr__(
            self, "manifest_digest", _require_hash(self.manifest_digest, "manifest_digest")
        )
        object.__setattr__(self, "input_hash", _require_hash(self.input_hash, "input_hash"))
        object.__setattr__(self, "output_hash", _require_hash(self.output_hash, "output_hash"))

        evidence = self.evidence_drafts if self.evidence_drafts is not None else self.evidence
        provenance = (
            self.provenance_drafts if self.provenance_drafts is not None else self.provenance
        )
        artifact_alias = self.artifact_summaries
        if self.artifact_drafts is not None:
            if artifact_alias is not None and tuple(artifact_alias) != tuple(self.artifact_drafts):
                raise ValueError("artifact_summaries and artifact_drafts disagree")
            artifact_alias = self.artifact_drafts
        artifacts = artifact_alias if artifact_alias is not None else self.artifacts
        if (
            self.evidence_drafts is not None
            and self.evidence
            and tuple(self.evidence) != tuple(self.evidence_drafts)
        ):
            raise ValueError("evidence and evidence_drafts disagree")
        if (
            self.provenance_drafts is not None
            and self.provenance
            and tuple(self.provenance) != tuple(self.provenance_drafts)
        ):
            raise ValueError("provenance and provenance_drafts disagree")
        if (
            artifact_alias is not None
            and self.artifacts
            and tuple(self.artifacts) != tuple(artifact_alias)
        ):
            raise ValueError("artifacts and artifact aliases disagree")
        if any(not isinstance(item, EvidenceDraft) for item in evidence):
            raise TypeError("receipt evidence must contain EvidenceDraft")
        if any(not isinstance(item, ProvenanceDraft) for item in provenance):
            raise TypeError("receipt provenance must contain ProvenanceDraft")
        if any(not isinstance(item, ArtifactReceipt) for item in artifacts):
            raise TypeError("receipt artifacts must contain ArtifactReceipt")
        object.__setattr__(self, "evidence", tuple(evidence))
        object.__setattr__(self, "provenance", tuple(provenance))
        object.__setattr__(self, "artifacts", tuple(artifacts))
        object.__setattr__(self, "evidence_drafts", None)
        object.__setattr__(self, "provenance_drafts", None)
        object.__setattr__(self, "artifact_summaries", None)
        object.__setattr__(self, "artifact_drafts", None)
        object.__setattr__(self, "scientific_task_id", None)
        proof = self.authenticity_proof
        if self.signature is not None:
            if proof is not None and proof != self.signature:
                raise ValueError("authenticity_proof and signature disagree")
            proof = self.signature
        object.__setattr__(self, "signature", None)
        object.__setattr__(
            self,
            "authenticity_proof",
            _require_bounded_text(proof, "authenticity_proof", maximum=4096),
        )

    @property
    def canonical_input_hash(self) -> str:
        return self.input_hash

    @property
    def canonical_output_hash(self) -> str:
        return self.output_hash

    @property
    def task_scientific_id(self) -> str:
        return self.task_id

    def to_summary(self) -> dict[str, Any]:
        """供 Attempt/Invocation 使用的非敏感收据摘要，不含 proof/signature。"""

        return {
            "receipt_id": self.receipt_id,
            "issuer": self.issuer,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "invocation_id": self.invocation_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "manifest_digest": self.manifest_digest,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "result_status": self.result_status,
            "issued_at": self.issued_at.isoformat(),
            "evidence_count": len(self.evidence),
            "provenance_count": len(self.provenance),
            "artifact_ids": [item.artifact_id for item in self.artifacts],
        }

    def to_dict(self) -> dict[str, Any]:
        """序列化收据的可审计内容；真实性 proof 由 verifier 专用，不默认外泄。"""

        return {
            **self.to_summary(),
            "evidence": [item.to_dict() for item in self.evidence],
            "provenance": [item.to_dict() for item in self.provenance],
            "artifacts": [item.to_dict() for item in self.artifacts],
        }

    def to_wire_dict(self) -> dict[str, Any]:
        """供受信适配器边界使用的完整字典；不包含任何 verifier secret。"""

        return {**self.to_dict(), "authenticity_proof": self.authenticity_proof}


# 直观的兼容命名；类型本身仍只有一份契约。
CapabilityReceipt = ExecutionReceipt
Receipt = ExecutionReceipt
ArtifactDraft = ArtifactReceipt
ArtifactSummary = ArtifactReceipt
ReceiptArtifact = ArtifactReceipt
ReceiptArtifactSummary = ArtifactReceipt
EvidenceReceiptDraft = EvidenceDraft
EvidenceDraftRecord = EvidenceDraft
ProvenanceReceiptDraft = ProvenanceDraft
ProvenanceDraftRecord = ProvenanceDraft


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    """统一能力结果包；不代表科学结论已经通过人工或领域审核。"""

    capability_id: str
    status: CapabilityResultStatus
    output: Mapping[str, Any] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    hashes: Mapping[str, str] = field(default_factory=dict)
    error: str | None = None
    attempt_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    receipt: ExecutionReceipt | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capability_id", _require_text(self.capability_id, "capability_id")
        )
        status = self.status
        if not isinstance(status, CapabilityResultStatus):
            try:
                status = CapabilityResultStatus(str(status))
            except ValueError as exc:
                raise ValueError(f"unknown capability result status: {self.status!r}") from exc
        object.__setattr__(self, "status", status)
        if not isinstance(self.output, Mapping):
            raise TypeError("output must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        if not isinstance(self.hashes, Mapping):
            raise TypeError("hashes must be a mapping")
        object.__setattr__(self, "output", _freeze_value(self.output))
        object.__setattr__(self, "metadata", _freeze_value(self.metadata))
        object.__setattr__(self, "evidence", _freeze_texts(self.evidence, "evidence"))
        object.__setattr__(self, "artifacts", _freeze_texts(self.artifacts, "artifacts"))
        object.__setattr__(self, "provenance", _freeze_texts(self.provenance, "provenance"))
        frozen_hashes: dict[str, str] = {}
        for key, value in self.hashes.items():
            frozen_hashes[_require_text(str(key), "hash key")] = _require_text(value, "hash value")
        object.__setattr__(self, "hashes", MappingProxyType(frozen_hashes))
        if self.error is not None and not isinstance(self.error, str):
            raise TypeError("error must be a string or None")
        if self.attempt_id is not None:
            object.__setattr__(self, "attempt_id", _require_text(self.attempt_id, "attempt_id"))
        if self.receipt is not None and not isinstance(self.receipt, ExecutionReceipt):
            raise TypeError("receipt must be an ExecutionReceipt or None")
        if status == CapabilityResultStatus.SUCCEEDED and self.error:
            raise ValueError("succeeded result must not carry an error")
        if (
            status
            in {
                CapabilityResultStatus.FAILED,
                CapabilityResultStatus.TIMED_OUT,
                CapabilityResultStatus.INTERRUPTED,
                CapabilityResultStatus.BLOCKED,
            }
            and not self.error
        ):
            raise ValueError("failed, timed out, interrupted or blocked result requires an error")

    @property
    def ok(self) -> bool:
        return self.status == CapabilityResultStatus.SUCCEEDED

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "capability_id": self.capability_id,
            "status": self.status.value,
            "output": _thaw_value(self.output),
            "evidence": list(self.evidence),
            "artifacts": list(self.artifacts),
            "provenance": list(self.provenance),
            "hashes": dict(self.hashes),
            "error": self.error,
            "attempt_id": self.attempt_id,
            "metadata": _thaw_value(self.metadata),
        }
        # wzf：结果摘要允许证明收据的非敏感身份与哈希，但绝不把 proof/signature
        # 放入 Attempt/Invocation 或普通 API 响应。
        if self.receipt is not None:
            payload["receipt"] = self.receipt.to_summary()
        return payload


__all__ = [
    "CapabilityInvocationRequest",
    "ArtifactDraft",
    "ArtifactReceipt",
    "ArtifactSummary",
    "CapabilityReceipt",
    "CapabilityResult",
    "CapabilityResultStatus",
    "CapabilityStatus",
    "CompletionContract",
    "EvidenceContract",
    "EvidenceDraft",
    "EvidenceDraftRecord",
    "EvidenceReceiptDraft",
    "ExecutionReceipt",
    "ExecutionStatus",
    "PermissionDecision",
    "ResourceRequest",
    "ProvenanceDraft",
    "ProvenanceDraftRecord",
    "ProvenanceReceiptDraft",
    "Receipt",
    "ReceiptArtifact",
    "ReceiptArtifactSummary",
    "canonical_hash",
    "canonical_json_hash",
]
