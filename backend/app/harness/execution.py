"""Session 原生 Capability 的安全应用层执行契约。

本模块不连接数据库、Worker 或旧 ``ResearchRun``。它只把一次已授权调用转换为
TaskGraph、ScientificTask、ExecutionAttempt、Outbox 与 Evidence 的原子持久化意图。
运行时接线、Outbox 消费、Graph 投影和真实 Artifact 校验均由后续适配层实现；缺少
任一可信依赖时服务 fail-closed，不会把调用方文本当审批、证据或恢复事实。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from app.harness.capabilities import CapabilityManifest
from app.harness.contracts import (
    CapabilityResult,
    CapabilityResultStatus,
    ExecutionReceipt,
    PermissionDecision,
)


_TERMINAL_STATUSES = frozenset(
    {
        "succeeded",
        "failed",
        "timed_out",
        "cancelled",
        "interrupted",
        "blocked",
    }
)
_RETRYABLE_INVOCATION_STATUSES = frozenset({"failed", "timed_out", "interrupted", "blocked"})


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _require_text(value: object, field_name: str, *, maximum: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if maximum is not None and len(text) > maximum:
        raise ValueError(f"{field_name} must be at most {maximum} characters")
    return text


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_thaw(item) for item in value]
    return value


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        _thaw(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _secret_reference_hash(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _failure_fingerprint(error_type: str, error_message: str) -> str:
    return _canonical_hash({"error_type": error_type, "error_message": error_message})


class CapabilityRegistry(Protocol):
    def get_manifest(self, capability_id: str) -> CapabilityManifest | None: ...


class InvocationRepository(Protocol):
    """仓储适配器必须把一次调用中的全部 Intent 放在同一事务提交。"""

    def find_by_idempotency(
        self, user_id: str, session_id: str, idempotency_key: str
    ) -> InvocationRecord | None: ...

    def find_attempt_by_idempotency(
        self, scientific_task_id: str, idempotency_key: str
    ) -> ExecutionAttemptRecord | None: ...

    def get_invocation(self, invocation_id: str) -> InvocationRecord | None: ...

    def get_task_graph(self, task_graph_id: str) -> TaskGraphRecord | None: ...

    def get_scientific_task(self, scientific_task_id: str) -> ScientificTaskRecord | None: ...

    def get_attempt(self, attempt_id: str) -> ExecutionAttemptRecord | None: ...

    def persist_atomically(self, intents: tuple[PersistenceIntent, ...]) -> None: ...


class SyncExecutor(Protocol):
    def execute(
        self,
        manifest: CapabilityManifest,
        inputs: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> CapabilityResult: ...


class PermissionEvaluator(Protocol):
    def evaluate(
        self,
        *,
        user_id: str,
        session_id: str,
        manifest: CapabilityManifest,
        inputs: Mapping[str, Any],
        approval_reference: str | None,
    ) -> Sequence[PermissionDecision]: ...


class ContextValidator(Protocol):
    def validate_execution(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_id: str | None,
        goal_id: str | None,
        skill_execution_id: str | None,
    ) -> ContextValidation: ...


class RetryPolicy(Protocol):
    def evaluate(
        self,
        *,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        parent_attempt: ExecutionAttemptRecord,
        diagnosis: str,
        diagnostic_evidence_ids: tuple[str, ...],
        now: datetime,
    ) -> RetryPolicyDecision: ...


class EvidenceGate(Protocol):
    def verify(
        self,
        *,
        manifest: CapabilityManifest,
        result: CapabilityResult,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
    ) -> EvidenceGateDecision: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class IDFactory(Protocol):
    def new_id(self, kind: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ContextValidation:
    allowed: bool
    reason: str
    policy_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be a bool")
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        if self.allowed and self.policy_id is None:
            raise ValueError("allowed context validation must identify policy_id")
        if self.policy_id is not None:
            object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))


@dataclass(frozen=True, slots=True)
class RetryPolicyDecision:
    status: str
    reason: str
    policy_id: str
    evidence_snapshot_hash: str | None = None
    available_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {"allowed", "waiting_for_backoff", "denied"}:
            raise ValueError("unknown retry policy status")
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        if self.status == "allowed" and not self.evidence_snapshot_hash:
            raise ValueError("allowed retry requires an evidence snapshot hash")
        if self.evidence_snapshot_hash is not None:
            object.__setattr__(
                self,
                "evidence_snapshot_hash",
                _require_text(self.evidence_snapshot_hash, "evidence_snapshot_hash", maximum=128),
            )


@dataclass(frozen=True, slots=True)
class VerifiedEvidenceSpec:
    evidence_type: str
    statement: str
    artifact_ids: tuple[str, ...]
    source_uri: str | None
    content_hash: str
    reviewer_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence_type", _require_text(self.evidence_type, "evidence_type")
        )
        object.__setattr__(self, "statement", _require_text(self.statement, "statement"))
        object.__setattr__(self, "content_hash", _require_text(self.content_hash, "content_hash"))
        artifact_ids = tuple(_require_text(item, "artifact_id") for item in self.artifact_ids)
        if len(set(artifact_ids)) != len(artifact_ids):
            raise ValueError("artifact_ids must not contain duplicates")
        object.__setattr__(self, "artifact_ids", artifact_ids)
        if self.source_uri is not None:
            object.__setattr__(self, "source_uri", _require_text(self.source_uri, "source_uri"))
        if self.reviewer_id is not None:
            object.__setattr__(self, "reviewer_id", _require_text(self.reviewer_id, "reviewer_id"))
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class VerifiedProvenanceSpec:
    source_type: str
    source_id: str
    relation_type: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in ("source_type", "source_id", "relation_type"):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class EvidenceGateDecision:
    allowed: bool
    reason: str
    policy_id: str
    evidence: tuple[VerifiedEvidenceSpec, ...] = ()
    provenance: tuple[VerifiedProvenanceSpec, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be a bool")
        object.__setattr__(self, "reason", _require_text(self.reason, "reason"))
        object.__setattr__(self, "policy_id", _require_text(self.policy_id, "policy_id"))
        if any(not isinstance(item, VerifiedEvidenceSpec) for item in self.evidence):
            raise TypeError("evidence must contain VerifiedEvidenceSpec")
        if any(not isinstance(item, VerifiedProvenanceSpec) for item in self.provenance):
            raise TypeError("provenance must contain VerifiedProvenanceSpec")


@dataclass(frozen=True, slots=True)
class ExecuteCapabilityCommand:
    capability_id: str
    session_id: str
    user_id: str
    inputs: Mapping[str, Any]
    idempotency_key: str
    turn_id: str | None = None
    goal_id: str | None = None
    skill_execution_id: str | None = None
    approval_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "capability_id", _require_text(self.capability_id, "capability_id", maximum=160)
        )
        for field_name in ("session_id", "user_id"):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "idempotency_key",
            _require_text(self.idempotency_key, "idempotency_key", maximum=240),
        )
        if not isinstance(self.inputs, Mapping):
            raise TypeError("inputs must be a mapping")
        object.__setattr__(self, "inputs", _freeze(self.inputs))
        for field_name in ("turn_id", "goal_id", "skill_execution_id", "approval_reference"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _require_text(value, field_name))


@dataclass(frozen=True, slots=True)
class RetryCapabilityCommand:
    invocation_id: str
    session_id: str
    user_id: str
    idempotency_key: str
    diagnosis: str
    diagnostic_evidence_ids: tuple[str, ...]
    approval_reference: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("invocation_id", "session_id", "user_id"):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        object.__setattr__(
            self,
            "idempotency_key",
            _require_text(self.idempotency_key, "idempotency_key", maximum=240),
        )
        object.__setattr__(self, "diagnosis", _require_text(self.diagnosis, "diagnosis"))
        evidence_ids = tuple(
            _require_text(item, "diagnostic_evidence_id") for item in self.diagnostic_evidence_ids
        )
        if not evidence_ids:
            raise ValueError("diagnostic_evidence_ids must not be empty")
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("diagnostic_evidence_ids must not contain duplicates")
        object.__setattr__(self, "diagnostic_evidence_ids", evidence_ids)
        if self.approval_reference is not None:
            object.__setattr__(
                self,
                "approval_reference",
                _require_text(self.approval_reference, "approval_reference"),
            )


@dataclass(frozen=True, slots=True)
class InvocationRecord:
    id: str
    session_id: str
    user_id: str
    capability_id: str
    capability_version: str
    manifest_digest: str
    execution_mode: str
    risk_level: str
    status: str
    inputs: Mapping[str, Any]
    request_hash: str
    permission_decisions: tuple[PermissionDecision, ...]
    idempotency_key: str
    approval_reference_hash: str | None = None
    turn_id: str | None = None
    goal_id: str | None = None
    skill_execution_id: str | None = None
    task_graph_id: str | None = None
    scientific_task_id: str | None = None
    execution_attempt_id: str | None = None
    result: Mapping[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "inputs", _freeze(self.inputs))
        object.__setattr__(self, "result", _freeze(self.result))


@dataclass(frozen=True, slots=True)
class TaskGraphRecord:
    id: str
    session_id: str
    user_id: str
    name: str
    status: str
    revision: int = 1
    goal_id: str | None = None
    skill_execution_id: str | None = None
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise ValueError("revision must be positive")


@dataclass(frozen=True, slots=True)
class ScientificTaskRecord:
    id: str
    task_graph_id: str
    user_id: str
    task_key: str
    name: str
    task_type: str
    status: str
    inputs: Mapping[str, Any]
    completion_contract: Mapping[str, Any]
    resource_request: Mapping[str, Any]
    idempotency_key: str
    skill_execution_id: str | None = None
    created_at: datetime = field(default_factory=_now_utc)
    updated_at: datetime = field(default_factory=_now_utc)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_key", _require_text(self.task_key, "task_key", maximum=160))
        object.__setattr__(
            self,
            "idempotency_key",
            _require_text(self.idempotency_key, "idempotency_key", maximum=240),
        )
        object.__setattr__(self, "inputs", _freeze(self.inputs))
        object.__setattr__(self, "completion_contract", _freeze(self.completion_contract))
        object.__setattr__(self, "resource_request", _freeze(self.resource_request))


@dataclass(frozen=True, slots=True)
class ExecutionAttemptRecord:
    id: str
    scientific_task_id: str
    user_id: str
    attempt_no: int
    status: str
    idempotency_key: str
    inputs: Mapping[str, Any]
    output: Mapping[str, Any] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None
    failure_fingerprint: str | None = None
    parent_attempt_id: str | None = None
    diagnosis: str | None = None
    diagnostic_evidence_ids: tuple[str, ...] = ()
    retry_payload_hash: str | None = None
    retry_policy_id: str | None = None
    retry_evidence_snapshot_hash: str | None = None
    available_at: datetime | None = None
    created_at: datetime = field(default_factory=_now_utc)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.attempt_no < 1:
            raise ValueError("attempt_no must be positive")
        if self.status not in {
            "queued",
            "running",
            "waiting_for_approval",
            "waiting_for_resource",
            "waiting_for_dependency",
            "waiting_for_input",
            "succeeded",
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
            "blocked",
        }:
            raise ValueError("unknown attempt status")
        if self.status in _TERMINAL_STATUSES and self.finished_at is None:
            raise ValueError("terminal attempt requires finished_at")
        object.__setattr__(
            self,
            "idempotency_key",
            _require_text(self.idempotency_key, "idempotency_key", maximum=240),
        )
        object.__setattr__(self, "inputs", _freeze(self.inputs))
        object.__setattr__(self, "output", _freeze(self.output))
        evidence_ids = tuple(
            _require_text(item, "diagnostic_evidence_id") for item in self.diagnostic_evidence_ids
        )
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError("diagnostic_evidence_ids must not contain duplicates")
        object.__setattr__(self, "diagnostic_evidence_ids", evidence_ids)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    id: str
    user_id: str
    scientific_task_id: str
    attempt_id: str
    evidence_type: str
    status: str
    statement: str
    artifact_ids: tuple[str, ...]
    source_uri: str | None
    content_hash: str
    reviewer_id: str | None
    sufficient: bool
    metadata: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class ProvenanceRecord:
    id: str
    user_id: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    relation_type: str
    metadata: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    id: str
    dedupe_key: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: Mapping[str, Any]
    status: str
    pending_at: datetime
    available_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))


@dataclass(frozen=True, slots=True)
class InvocationIntent:
    invocation: InvocationRecord
    kind: str = field(default="invocation", init=False)


@dataclass(frozen=True, slots=True)
class TaskGraphIntent:
    task_graph: TaskGraphRecord
    kind: str = field(default="task_graph", init=False)


@dataclass(frozen=True, slots=True)
class ScientificTaskIntent:
    task: ScientificTaskRecord
    kind: str = field(default="scientific_task", init=False)


@dataclass(frozen=True, slots=True)
class ExecutionAttemptIntent:
    attempt: ExecutionAttemptRecord
    kind: str = field(default="execution_attempt", init=False)


@dataclass(frozen=True, slots=True)
class EvidenceIntent:
    evidence: EvidenceRecord
    kind: str = field(default="evidence", init=False)


@dataclass(frozen=True, slots=True)
class ProvenanceIntent:
    provenance: ProvenanceRecord
    kind: str = field(default="provenance", init=False)


@dataclass(frozen=True, slots=True)
class OutboxIntent:
    outbox: OutboxRecord
    kind: str = field(default="outbox", init=False)


PersistenceIntent = (
    InvocationIntent
    | TaskGraphIntent
    | ScientificTaskIntent
    | ExecutionAttemptIntent
    | EvidenceIntent
    | ProvenanceIntent
    | OutboxIntent
)


@dataclass(frozen=True, slots=True)
class ExecutionDecision:
    status: str
    invocation: InvocationRecord | None = None
    task_graph: TaskGraphRecord | None = None
    scientific_task: ScientificTaskRecord | None = None
    attempt: ExecutionAttemptRecord | None = None
    outbox: OutboxRecord | None = None
    output: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    reused: bool = False

    @property
    def invocation_id(self) -> str | None:
        return self.invocation.id if self.invocation is not None else None


class _DefaultClock:
    def now(self) -> datetime:
        return _now_utc()


class _DefaultIDFactory:
    def new_id(self, kind: str) -> str:
        return f"{kind}-{uuid4()}"


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _validate_json_value(value: Any, schema: Mapping[str, Any], path: str = "value") -> None:
    if "anyOf" in schema:
        if not any(_schema_matches(value, branch, path) for branch in schema["anyOf"]):
            raise ValueError(f"{path} does not match anyOf")
    if "oneOf" in schema:
        if sum(_schema_matches(value, branch, path) for branch in schema["oneOf"]) != 1:
            raise ValueError(f"{path} does not match exactly one oneOf branch")
    for branch in schema.get("allOf", ()):
        _validate_json_value(value, branch, path)
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} does not match const")
    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        if not any(_json_type_matches(value, item) for item in expected_types):
            raise ValueError(f"{path} has invalid type")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"{path}.{name} is required")
        extras = set(value) - set(properties)
        additional = schema.get("additionalProperties", True)
        if additional is False and extras:
            raise ValueError(f"{path} contains unknown fields: {sorted(extras)}")
        for name, item in value.items():
            child = properties.get(name)
            if isinstance(child, Mapping):
                _validate_json_value(item, child, f"{path}.{name}")
            elif isinstance(additional, Mapping):
                _validate_json_value(item, additional, f"{path}.{name}")
    if isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{path} has fewer than minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path} has more than maxItems")
        if isinstance(schema.get("items"), Mapping):
            for index, item in enumerate(value):
                _validate_json_value(item, schema["items"], f"{path}[{index}]")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path} is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path} is longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path} does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path} is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path} is above maximum")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            raise ValueError(f"{path} is not above exclusiveMinimum")
        if "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]:
            raise ValueError(f"{path} is not below exclusiveMaximum")


def _schema_matches(value: Any, schema: Mapping[str, Any], path: str) -> bool:
    try:
        _validate_json_value(value, schema, path)
    except ValueError:
        return False
    return True


class SessionCapabilityExecutor:
    """以显式激活清单执行 Session 能力；缺少可信依赖时一律拒绝。"""

    def __init__(
        self,
        registry: CapabilityRegistry | Mapping[str, CapabilityManifest],
        repository: InvocationRepository,
        sync_executor: SyncExecutor,
        permission_evaluator: PermissionEvaluator,
        context_validator: ContextValidator,
        retry_policy: RetryPolicy,
        evidence_gate: EvidenceGate,
        *,
        activated_capabilities: frozenset[str],
        clock: Clock | None = None,
        id_factory: IDFactory | None = None,
    ) -> None:
        self.registry = registry
        self.repository = repository
        self.sync_executor = sync_executor
        self.permission_evaluator = permission_evaluator
        self.context_validator = context_validator
        self.retry_policy = retry_policy
        self.evidence_gate = evidence_gate
        self.activated_capabilities = frozenset(activated_capabilities)
        self.clock = clock or _DefaultClock()
        self.id_factory = id_factory or _DefaultIDFactory()

    def _manifest(self, capability_id: str) -> CapabilityManifest | None:
        if isinstance(self.registry, Mapping):
            return self.registry.get(capability_id)
        return self.registry.get_manifest(capability_id)

    def _validate_context(self, command: ExecuteCapabilityCommand) -> ContextValidation:
        try:
            decision = self.context_validator.validate_execution(
                user_id=command.user_id,
                session_id=command.session_id,
                turn_id=command.turn_id,
                goal_id=command.goal_id,
                skill_execution_id=command.skill_execution_id,
            )
        except Exception as exc:
            return ContextValidation(False, f"context validator unavailable: {type(exc).__name__}")
        if not isinstance(decision, ContextValidation):
            return ContextValidation(False, "context validator returned an invalid decision")
        return decision

    def _permission_decisions(
        self,
        *,
        command: ExecuteCapabilityCommand | RetryCapabilityCommand,
        manifest: CapabilityManifest,
        inputs: Mapping[str, Any],
    ) -> tuple[tuple[PermissionDecision, ...], str | None]:
        try:
            raw = tuple(
                self.permission_evaluator.evaluate(
                    user_id=command.user_id,
                    session_id=command.session_id,
                    manifest=manifest,
                    inputs=inputs,
                    approval_reference=command.approval_reference,
                )
            )
        except Exception as exc:
            return (), f"permission evaluator unavailable: {type(exc).__name__}"
        if any(not isinstance(item, PermissionDecision) for item in raw):
            return (), "permission evaluator returned an invalid decision"
        return raw, None

    @staticmethod
    def _permission_status(
        manifest: CapabilityManifest,
        decisions: tuple[PermissionDecision, ...],
    ) -> tuple[str, str | None]:
        indexed: dict[str, PermissionDecision] = {}
        for decision in decisions:
            if decision.permission in indexed:
                return "permission_denied", "duplicate permission decision"
            indexed[decision.permission] = decision
        unexpected = set(indexed) - set(manifest.required_permissions)
        if unexpected:
            return "permission_denied", f"unexpected permission decisions: {sorted(unexpected)}"
        for permission in manifest.required_permissions:
            decision = indexed.get(permission)
            if decision is None:
                return "permission_denied", f"missing permission decision: {permission}"
            if decision.approval_required:
                return "waiting_for_approval", decision.reason or "policy approval required"
            if not decision.allowed:
                return "permission_denied", decision.reason or f"permission denied: {permission}"
        if manifest.approval_required:
            approval = indexed.get("policy.approval")
            if approval is None or not approval.allowed or approval.policy_id is None:
                return "waiting_for_approval", "manifest requires a verified policy approval"
        return "allowed", None

    def _persist(self, *intents: PersistenceIntent) -> None:
        if intents:
            self.repository.persist_atomically(tuple(intents))

    @staticmethod
    def _request_hash(command: ExecuteCapabilityCommand, manifest: CapabilityManifest) -> str:
        return _canonical_hash(
            {
                "capability_id": manifest.capability_id,
                "capability_version": manifest.version,
                "manifest_digest": manifest.digest,
                "execution_mode": manifest.execution_mode,
                "session_id": command.session_id,
                "user_id": command.user_id,
                "turn_id": command.turn_id,
                "goal_id": command.goal_id,
                "skill_execution_id": command.skill_execution_id,
                "inputs": command.inputs,
            }
        )

    def _base_invocation(
        self,
        command: ExecuteCapabilityCommand,
        manifest: CapabilityManifest,
        decisions: tuple[PermissionDecision, ...],
        status: str,
        request_hash: str,
        existing: InvocationRecord | None = None,
    ) -> InvocationRecord:
        now = self.clock.now()
        if existing is not None:
            return replace(
                existing,
                status=status,
                permission_decisions=decisions,
                approval_reference_hash=_secret_reference_hash(command.approval_reference),
                error_type=None,
                error_message=None,
                updated_at=now,
            )
        return InvocationRecord(
            id=self.id_factory.new_id("invocation"),
            session_id=command.session_id,
            user_id=command.user_id,
            turn_id=command.turn_id,
            goal_id=command.goal_id,
            skill_execution_id=command.skill_execution_id,
            capability_id=manifest.capability_id,
            capability_version=manifest.version,
            manifest_digest=manifest.digest,
            execution_mode=manifest.execution_mode,
            risk_level=manifest.risk_level,
            status=status,
            inputs=command.inputs,
            request_hash=request_hash,
            permission_decisions=decisions,
            idempotency_key=command.idempotency_key,
            approval_reference_hash=_secret_reference_hash(command.approval_reference),
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _records_are_consistent(
        invocation: InvocationRecord,
        graph: TaskGraphRecord,
        task: ScientificTaskRecord,
    ) -> bool:
        return (
            graph.id == invocation.task_graph_id
            and task.id == invocation.scientific_task_id
            and graph.session_id == invocation.session_id
            and graph.user_id == invocation.user_id
            and task.task_graph_id == graph.id
            and task.user_id == invocation.user_id
        )

    def _starting_graph_revision(self, graph: TaskGraphRecord) -> int:
        """新建 Graph 保持 revision=1，已有 Graph 才执行并发版本递增。"""

        if self.repository.get_task_graph(graph.id) is None:
            return graph.revision
        return graph.revision + 1

    def _ensure_graph_task(
        self,
        invocation: InvocationRecord,
        manifest: CapabilityManifest,
    ) -> tuple[InvocationRecord, TaskGraphRecord, ScientificTaskRecord]:
        has_graph = invocation.task_graph_id is not None
        has_task = invocation.scientific_task_id is not None
        if has_graph != has_task:
            raise RuntimeError("invocation has a partial task graph reference")
        if has_graph and has_task:
            graph = self.repository.get_task_graph(invocation.task_graph_id or "")
            task = self.repository.get_scientific_task(invocation.scientific_task_id or "")
            if (
                graph is None
                or task is None
                or not self._records_are_consistent(invocation, graph, task)
            ):
                raise RuntimeError("invocation task graph ownership is inconsistent")
            return invocation, graph, task
        now = self.clock.now()
        graph = TaskGraphRecord(
            id=self.id_factory.new_id("task-graph"),
            session_id=invocation.session_id,
            user_id=invocation.user_id,
            goal_id=invocation.goal_id,
            skill_execution_id=invocation.skill_execution_id,
            name=f"Capability {manifest.capability_id}",
            status="planned",
            created_at=now,
            updated_at=now,
        )
        task = ScientificTaskRecord(
            id=self.id_factory.new_id("scientific-task"),
            task_graph_id=graph.id,
            user_id=invocation.user_id,
            skill_execution_id=invocation.skill_execution_id,
            task_key=f"capability:{_canonical_hash({'capability_id': manifest.capability_id, 'idempotency_key': invocation.idempotency_key})}",
            name=manifest.description,
            task_type=manifest.capability_id,
            status="planned",
            inputs=invocation.inputs,
            completion_contract=manifest.completion_contract.to_dict(),
            resource_request=manifest.resource_profile,
            idempotency_key=invocation.idempotency_key,
            created_at=now,
            updated_at=now,
        )
        invocation = replace(
            invocation,
            task_graph_id=graph.id,
            scientific_task_id=task.id,
            updated_at=now,
        )
        # wzf：新 Graph/Task 只在内存中组装，交给 _run_sync/_queue_async 与
        # Invocation、Attempt（以及异步 Outbox）一次性提交。若在这里提前写库，
        # 进程可能在 Outbox 落盘前崩溃，留下无法被幂等重放修复的“queued 空壳”。
        return invocation, graph, task

    def execute(self, command: ExecuteCapabilityCommand) -> ExecutionDecision:
        manifest = self._manifest(command.capability_id)
        if manifest is None:
            return ExecutionDecision(status="missing_manifest", error="unknown capability")
        if command.capability_id not in self.activated_capabilities or not manifest.contract_ready:
            return ExecutionDecision(
                status="unavailable", error="capability runtime is not activated"
            )
        context = self._validate_context(command)
        if not context.allowed:
            return ExecutionDecision(status="invalid_context", error=context.reason)
        try:
            _validate_json_value(command.inputs, manifest.input_schema, "input")
        except ValueError as exc:
            return ExecutionDecision(status="invalid_input", error=str(exc))

        request_hash = self._request_hash(command, manifest)
        existing = self.repository.find_by_idempotency(
            command.user_id, command.session_id, command.idempotency_key
        )
        if existing is not None:
            if existing.request_hash != request_hash or existing.manifest_digest != manifest.digest:
                return ExecutionDecision(
                    status="idempotency_conflict",
                    invocation=existing,
                    error="idempotency key is bound to a different canonical request",
                )
            if existing.status != "waiting_for_approval":
                return ExecutionDecision(status="reused", invocation=existing, reused=True)

        decisions, evaluation_error = self._permission_decisions(
            command=command,
            manifest=manifest,
            inputs=command.inputs,
        )
        permission_status, reason = self._permission_status(manifest, decisions)
        if evaluation_error is not None:
            permission_status, reason = "permission_denied", evaluation_error
        if permission_status != "allowed":
            invocation_status = (
                "waiting_for_approval"
                if permission_status == "waiting_for_approval"
                else "rejected"
            )
            invocation = self._base_invocation(
                command,
                manifest,
                decisions,
                invocation_status,
                request_hash,
                existing=existing,
            )
            invocation = replace(
                invocation,
                error_type=None
                if invocation_status == "waiting_for_approval"
                else "PermissionDenied",
                error_message=reason,
            )
            self._persist(InvocationIntent(invocation))
            return ExecutionDecision(status=permission_status, invocation=invocation, error=reason)

        invocation = self._base_invocation(
            command,
            manifest,
            decisions,
            "queued",
            request_hash,
            existing=existing,
        )
        try:
            invocation, graph, task = self._ensure_graph_task(invocation, manifest)
        except RuntimeError as exc:
            return ExecutionDecision(status="invalid_state", invocation=invocation, error=str(exc))
        if manifest.execution_mode == "sync":
            return self._run_sync(
                invocation, graph, task, manifest, parent=None, retry=None, retry_decision=None
            )
        return self._queue_async(
            invocation, graph, task, manifest, parent=None, retry=None, retry_decision=None
        )

    def _new_attempt(
        self,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        *,
        status: str,
        idempotency_key: str,
        parent: ExecutionAttemptRecord | None,
        retry: RetryCapabilityCommand | None,
        retry_decision: RetryPolicyDecision | None,
    ) -> ExecutionAttemptRecord:
        now = self.clock.now()
        retry_payload_hash = None
        if retry is not None:
            retry_payload_hash = self._retry_payload_hash(invocation, parent, retry)
        return ExecutionAttemptRecord(
            id=self.id_factory.new_id("attempt"),
            scientific_task_id=task.id,
            user_id=invocation.user_id,
            attempt_no=parent.attempt_no + 1 if parent is not None else 1,
            status=status,
            idempotency_key=idempotency_key,
            inputs=invocation.inputs,
            parent_attempt_id=parent.id if parent is not None else None,
            diagnosis=retry.diagnosis if retry is not None else None,
            diagnostic_evidence_ids=retry.diagnostic_evidence_ids if retry is not None else (),
            retry_payload_hash=retry_payload_hash,
            retry_policy_id=retry_decision.policy_id if retry_decision is not None else None,
            retry_evidence_snapshot_hash=(
                retry_decision.evidence_snapshot_hash if retry_decision is not None else None
            ),
            available_at=retry_decision.available_at if retry_decision is not None else now,
            created_at=now,
            started_at=now if status == "running" else None,
        )

    def _run_sync(
        self,
        invocation: InvocationRecord,
        graph: TaskGraphRecord,
        task: ScientificTaskRecord,
        manifest: CapabilityManifest,
        *,
        parent: ExecutionAttemptRecord | None,
        retry: RetryCapabilityCommand | None,
        retry_decision: RetryPolicyDecision | None,
    ) -> ExecutionDecision:
        key = retry.idempotency_key if retry is not None else invocation.idempotency_key
        attempt = self._new_attempt(
            invocation,
            task,
            status="running",
            idempotency_key=key,
            parent=parent,
            retry=retry,
            retry_decision=retry_decision,
        )
        now = self.clock.now()
        invocation = replace(
            invocation,
            status="running",
            execution_attempt_id=attempt.id,
            started_at=invocation.started_at or now,
            finished_at=None,
            updated_at=now,
        )
        graph = replace(
            graph,
            status="running",
            revision=self._starting_graph_revision(graph),
            updated_at=now,
        )
        task = replace(task, status="running", updated_at=now)
        self._persist(
            ExecutionAttemptIntent(attempt),
            InvocationIntent(invocation),
            TaskGraphIntent(graph),
            ScientificTaskIntent(task),
        )
        try:
            result = self.sync_executor.execute(
                manifest,
                invocation.inputs,
                {
                    "session_id": invocation.session_id,
                    "user_id": invocation.user_id,
                    "invocation_id": invocation.id,
                    "task_id": task.id,
                    "attempt_id": attempt.id,
                    "capability_version": manifest.version,
                    "manifest_digest": manifest.digest,
                },
            )
        except Exception as exc:
            return self._finish_exception(invocation, graph, task, attempt, exc)
        if not isinstance(result, CapabilityResult):
            return self._finish_exception(
                invocation,
                graph,
                task,
                attempt,
                TypeError("sync executor must return CapabilityResult"),
            )
        try:
            self._validate_result_identity(manifest, result, attempt)
        except ValueError as exc:
            # wzf：新收据路径的身份不一致属于证据门拒绝；旧兼容结果无 Receipt
            # 仍保留原有 failed 回归语义，避免把历史适配器错误伪装成 blocked。
            if isinstance(result.receipt, ExecutionReceipt):
                return self._finish_blocked(invocation, graph, task, attempt, str(exc))
            return self._finish_exception(invocation, graph, task, attempt, exc)
        if not result.ok:
            return self._finish_capability_state(invocation, graph, task, attempt, result)

        try:
            gate = self.evidence_gate.verify(
                manifest=manifest,
                result=result,
                invocation=invocation,
                task=task,
                attempt=attempt,
            )
        except Exception as exc:
            gate = EvidenceGateDecision(
                False,
                f"evidence gate unavailable: {type(exc).__name__}",
                "evidence-gate-error",
            )
        if not isinstance(gate, EvidenceGateDecision) or not gate.allowed:
            reason = (
                gate.reason
                if isinstance(gate, EvidenceGateDecision)
                else "invalid evidence gate decision"
            )
            return self._finish_blocked(invocation, graph, task, attempt, reason)
        try:
            self._validate_success_contract(manifest, result, gate)
        except ValueError as exc:
            return self._finish_blocked(invocation, graph, task, attempt, str(exc))
        return self._finish_success(invocation, graph, task, attempt, result, gate)

    def _queue_async(
        self,
        invocation: InvocationRecord,
        graph: TaskGraphRecord,
        task: ScientificTaskRecord,
        manifest: CapabilityManifest,
        *,
        parent: ExecutionAttemptRecord | None,
        retry: RetryCapabilityCommand | None,
        retry_decision: RetryPolicyDecision | None,
    ) -> ExecutionDecision:
        key = retry.idempotency_key if retry is not None else invocation.idempotency_key
        attempt = self._new_attempt(
            invocation,
            task,
            status="queued",
            idempotency_key=key,
            parent=parent,
            retry=retry,
            retry_decision=retry_decision,
        )
        now = self.clock.now()
        invocation = replace(
            invocation,
            status="queued",
            execution_attempt_id=attempt.id,
            error_type=None,
            error_message=None,
            finished_at=None,
            updated_at=now,
        )
        graph = replace(
            graph,
            status="running",
            revision=self._starting_graph_revision(graph),
            updated_at=now,
        )
        task = replace(task, status="queued", updated_at=now)
        outbox = OutboxRecord(
            id=self.id_factory.new_id("outbox"),
            dedupe_key=f"attempt:{attempt.id}:queued",
            event_type="ExecutionAttemptQueued",
            aggregate_type="execution_attempt",
            aggregate_id=attempt.id,
            payload={
                "session_id": invocation.session_id,
                "user_id": invocation.user_id,
                "invocation_id": invocation.id,
                "task_graph_id": graph.id,
                "scientific_task_id": task.id,
                "attempt_id": attempt.id,
                "capability_id": manifest.capability_id,
                "manifest_digest": manifest.digest,
            },
            status="pending",
            pending_at=now,
            available_at=attempt.available_at or now,
        )
        # wzf：Attempt 与 Outbox 必须在同一事务持久化。此处不直接调用 Worker，
        # 避免“已入队但崩溃未投递”和“Worker 已接收却被本地标失败”两类双事实源。
        self._persist(
            ExecutionAttemptIntent(attempt),
            InvocationIntent(invocation),
            TaskGraphIntent(graph),
            ScientificTaskIntent(task),
            OutboxIntent(outbox),
        )
        return ExecutionDecision(
            status="queued",
            invocation=invocation,
            task_graph=graph,
            scientific_task=task,
            attempt=attempt,
            outbox=outbox,
        )

    def _finish_exception(
        self,
        invocation: InvocationRecord,
        graph: TaskGraphRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
        error: Exception,
    ) -> ExecutionDecision:
        finished = self.clock.now()
        error_type = type(error).__name__
        message = str(error) or error_type
        attempt = replace(
            attempt,
            status="failed",
            error_type=error_type,
            error_message=message,
            failure_fingerprint=_failure_fingerprint(error_type, message),
            finished_at=finished,
        )
        invocation = replace(
            invocation,
            status="failed",
            error_type=error_type,
            error_message=message,
            updated_at=finished,
            finished_at=finished,
        )
        task = replace(task, status="failed", updated_at=finished)
        # Graph 聚合态由后续 projector 根据所有节点/依赖计算；单节点失败不能
        # 直接覆盖整张图，也不能阻塞无关分支。
        self._persist(
            ExecutionAttemptIntent(attempt),
            InvocationIntent(invocation),
            ScientificTaskIntent(task),
        )
        return ExecutionDecision(
            status="failed",
            invocation=invocation,
            task_graph=graph,
            scientific_task=task,
            attempt=attempt,
            error=message,
        )

    def _finish_capability_state(
        self,
        invocation: InvocationRecord,
        graph: TaskGraphRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
        result: CapabilityResult,
    ) -> ExecutionDecision:
        status = result.status.value
        finished = self.clock.now() if status in _TERMINAL_STATUSES else None
        error_message = result.error
        error_type = (
            f"Capability{result.status.name.title().replace('_', '')}" if error_message else None
        )
        attempt = replace(
            attempt,
            status=status,
            output=result.to_dict(),
            error_type=error_type,
            error_message=error_message,
            failure_fingerprint=(
                _failure_fingerprint(error_type or status, error_message or status)
                if status in _RETRYABLE_INVOCATION_STATUSES
                else None
            ),
            finished_at=finished,
        )
        invocation = replace(
            invocation,
            status=status,
            result=result.to_dict(),
            error_type=error_type,
            error_message=error_message,
            updated_at=self.clock.now(),
            finished_at=finished,
        )
        task = replace(task, status=status, updated_at=self.clock.now())
        self._persist(
            ExecutionAttemptIntent(attempt),
            InvocationIntent(invocation),
            ScientificTaskIntent(task),
        )
        return ExecutionDecision(
            status=status,
            invocation=invocation,
            task_graph=graph,
            scientific_task=task,
            attempt=attempt,
            output=result.output,
            error=error_message,
        )

    def _finish_blocked(
        self,
        invocation: InvocationRecord,
        graph: TaskGraphRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
        reason: str,
    ) -> ExecutionDecision:
        result = CapabilityResult(
            capability_id=invocation.capability_id,
            status=CapabilityResultStatus.BLOCKED,
            error=reason,
            attempt_id=attempt.id,
        )
        return self._finish_capability_state(invocation, graph, task, attempt, result)

    def _finish_success(
        self,
        invocation: InvocationRecord,
        graph: TaskGraphRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
        result: CapabilityResult,
        gate: EvidenceGateDecision,
    ) -> ExecutionDecision:
        finished = self.clock.now()
        # wzf：Evidence/Provenance ID 必须在同一最终事务中预分配，并回写到
        # Attempt/Invocation 的结果摘要；不得先落库再让门反向“认领”证据。
        evidence_records: list[tuple[str, VerifiedEvidenceSpec]] = [
            (self.id_factory.new_id("evidence"), item) for item in gate.evidence
        ]
        provenance_records: list[tuple[str, VerifiedProvenanceSpec]] = [
            (self.id_factory.new_id("provenance"), item) for item in gate.provenance
        ]
        result_summary = result.to_dict()
        receipt = result.receipt
        if isinstance(receipt, ExecutionReceipt):
            result_summary["receipt_id"] = receipt.receipt_id
            result_summary["evidence_gate_policy_id"] = gate.policy_id
        result_summary["evidence_record_ids"] = [item[0] for item in evidence_records]
        result_summary["provenance_record_ids"] = [item[0] for item in provenance_records]
        # 旧读取端使用短键；它们与显式 record_ids 共享同一份已分配 ID。
        result_summary["evidence_ids"] = list(result_summary["evidence_record_ids"])
        result_summary["provenance_ids"] = list(result_summary["provenance_record_ids"])
        attempt = replace(
            attempt,
            status="succeeded",
            output=result_summary,
            finished_at=finished,
        )
        invocation = replace(
            invocation,
            status="succeeded",
            result=result_summary,
            error_type=None,
            error_message=None,
            updated_at=finished,
            finished_at=finished,
        )
        task = replace(task, status="succeeded", updated_at=finished)
        intents: list[PersistenceIntent] = [
            ExecutionAttemptIntent(attempt),
            InvocationIntent(invocation),
            ScientificTaskIntent(task),
        ]
        for evidence_id, item in evidence_records:
            intents.append(
                EvidenceIntent(
                    EvidenceRecord(
                        id=evidence_id,
                        user_id=invocation.user_id,
                        scientific_task_id=task.id,
                        attempt_id=attempt.id,
                        evidence_type=item.evidence_type,
                        status="verified",
                        statement=item.statement,
                        artifact_ids=item.artifact_ids,
                        source_uri=item.source_uri,
                        content_hash=item.content_hash,
                        reviewer_id=item.reviewer_id,
                        sufficient=True,
                        metadata={
                            **_thaw(item.metadata),
                            "evidence_gate_policy_id": gate.policy_id,
                            **(
                                {"receipt_id": receipt.receipt_id}
                                if isinstance(receipt, ExecutionReceipt)
                                else {}
                            ),
                        },
                        created_at=finished,
                    )
                )
            )
        for provenance_id, item in provenance_records:
            intents.append(
                ProvenanceIntent(
                    ProvenanceRecord(
                        id=provenance_id,
                        user_id=invocation.user_id,
                        source_type=item.source_type,
                        source_id=item.source_id,
                        target_type="execution_attempt",
                        target_id=attempt.id,
                        relation_type=item.relation_type,
                        metadata={
                            **_thaw(item.metadata),
                            **(
                                {"receipt_id": receipt.receipt_id}
                                if isinstance(receipt, ExecutionReceipt)
                                else {}
                            ),
                        },
                        created_at=finished,
                    )
                )
            )
        # wzf：同步能力也必须把“完成事实”作为同一外层事务中的 Outbox 事件落库，
        # 供投影/审计消费者异步读取；payload 只含身份和哈希，不复制科学结果原文。
        outbox = OutboxRecord(
            id=self.id_factory.new_id("outbox"),
            dedupe_key=f"invocation:{invocation.id}:succeeded",
            event_type="CapabilityInvocationSucceeded",
            aggregate_type="capability_invocation",
            aggregate_id=invocation.id,
            payload={
                "session_id": invocation.session_id,
                "user_id": invocation.user_id,
                "invocation_id": invocation.id,
                "task_graph_id": graph.id,
                "scientific_task_id": task.id,
                "attempt_id": attempt.id,
                "capability_id": invocation.capability_id,
                "manifest_digest": invocation.manifest_digest,
                "output_hash": result.hashes.get("output"),
                "receipt_id": receipt.receipt_id
                if isinstance(receipt, ExecutionReceipt)
                else None,
            },
            status="pending",
            pending_at=finished,
            available_at=finished,
        )
        intents.append(OutboxIntent(outbox))
        self._persist(*intents)
        return ExecutionDecision(
            status="succeeded",
            invocation=invocation,
            task_graph=graph,
            scientific_task=task,
            attempt=attempt,
            outbox=outbox,
            output=result.output,
        )

    @staticmethod
    def _validate_result_identity(
        manifest: CapabilityManifest,
        result: CapabilityResult,
        attempt: ExecutionAttemptRecord,
    ) -> None:
        if result.capability_id != manifest.capability_id:
            raise ValueError("result capability_id does not match manifest")
        if result.attempt_id != attempt.id:
            raise ValueError("result attempt_id does not match current attempt")
        if manifest.completion_contract.requires_terminal_status and not result.terminal:
            raise ValueError("completion contract requires a terminal result")
        if result.ok:
            _validate_json_value(result.output, manifest.output_schema, "output")

    @staticmethod
    def _validate_success_contract(
        manifest: CapabilityManifest,
        result: CapabilityResult,
        gate: EvidenceGateDecision,
    ) -> None:
        contract = manifest.completion_contract
        if result.status.value not in contract.accepted_statuses:
            raise ValueError("result status is not accepted by completion contract")
        missing_output = set(contract.required_output_fields) - set(result.output)
        if missing_output:
            raise ValueError(f"missing required output fields: {sorted(missing_output)}")
        verified_types = {item.evidence_type for item in gate.evidence}
        verified_artifacts = {
            artifact_id for item in gate.evidence for artifact_id in item.artifact_ids
        }
        if set(result.artifacts) - verified_artifacts:
            raise ValueError("result contains artifacts not verified by EvidenceGate")
        if set(contract.required_artifacts) - verified_artifacts:
            raise ValueError("completion contract artifacts are not verified")
        if len(verified_artifacts) < contract.minimum_artifact_count:
            raise ValueError("verified artifact count is below completion minimum")
        evidence = manifest.evidence_contract
        if set(evidence.required_evidence_types) - verified_types:
            raise ValueError("required evidence types are not verified")
        if set(evidence.required_artifacts) - verified_artifacts:
            raise ValueError("evidence contract artifacts are not verified")
        if len(gate.evidence) < evidence.minimum_evidence_count:
            raise ValueError("verified evidence count is below minimum")
        if len(verified_artifacts) < evidence.minimum_artifact_count:
            raise ValueError("verified artifact count is below evidence minimum")
        if evidence.require_provenance and not gate.provenance:
            raise ValueError("verified provenance is required")
        if evidence.require_hash and any(not item.content_hash for item in gate.evidence):
            raise ValueError("verified evidence hash is required")
        if evidence.reviewer_required and any(item.reviewer_id is None for item in gate.evidence):
            raise ValueError("verified reviewer is required")

    @staticmethod
    def _retry_payload_hash(
        invocation: InvocationRecord,
        parent: ExecutionAttemptRecord | None,
        command: RetryCapabilityCommand,
    ) -> str:
        return _canonical_hash(
            {
                "invocation_id": invocation.id,
                "capability_version": invocation.capability_version,
                "manifest_digest": invocation.manifest_digest,
                "parent_attempt_id": parent.id if parent is not None else None,
                "parent_failure_fingerprint": (
                    parent.failure_fingerprint if parent is not None else None
                ),
                "diagnosis": command.diagnosis,
                "diagnostic_evidence_ids": command.diagnostic_evidence_ids,
            }
        )

    def retry(self, command: RetryCapabilityCommand) -> ExecutionDecision:
        invocation = self.repository.get_invocation(command.invocation_id)
        if invocation is None:
            return ExecutionDecision(status="missing_invocation", error="unknown invocation")
        if invocation.session_id != command.session_id or invocation.user_id != command.user_id:
            return ExecutionDecision(
                status="permission_denied", error="invocation ownership mismatch"
            )
        if (
            not invocation.task_graph_id
            or not invocation.scientific_task_id
            or not invocation.execution_attempt_id
        ):
            return ExecutionDecision(
                status="invalid_state", invocation=invocation, error="retry links are incomplete"
            )
        graph = self.repository.get_task_graph(invocation.task_graph_id)
        task = self.repository.get_scientific_task(invocation.scientific_task_id)
        parent = self.repository.get_attempt(invocation.execution_attempt_id)
        if graph is None or task is None or parent is None:
            return ExecutionDecision(
                status="invalid_state", invocation=invocation, error="retry context is missing"
            )
        if not self._records_are_consistent(invocation, graph, task):
            return ExecutionDecision(
                status="permission_denied", invocation=invocation, error="retry ownership mismatch"
            )
        if parent.scientific_task_id != task.id or parent.user_id != invocation.user_id:
            return ExecutionDecision(
                status="permission_denied",
                invocation=invocation,
                error="parent attempt ownership mismatch",
            )
        manifest = self._manifest(invocation.capability_id)
        if (
            manifest is None
            or invocation.capability_id not in self.activated_capabilities
            or not manifest.contract_ready
            or manifest.version != invocation.capability_version
            or manifest.digest != invocation.manifest_digest
        ):
            return ExecutionDecision(
                status="invalid_state",
                invocation=invocation,
                error="manifest changed or runtime is not activated",
            )
        existing_attempt = self.repository.find_attempt_by_idempotency(
            task.id, command.idempotency_key
        )
        if existing_attempt is not None:
            replay_parent = (
                self.repository.get_attempt(existing_attempt.parent_attempt_id)
                if existing_attempt.parent_attempt_id is not None
                else None
            )
            replay_payload_hash = self._retry_payload_hash(invocation, replay_parent, command)
            if existing_attempt.retry_payload_hash != replay_payload_hash:
                return ExecutionDecision(
                    status="idempotency_conflict",
                    invocation=invocation,
                    attempt=existing_attempt,
                    error="retry idempotency key is bound to a different diagnosis",
                )
            return ExecutionDecision(
                status="reused",
                invocation=invocation,
                attempt=existing_attempt,
                reused=True,
            )
        if invocation.status not in _RETRYABLE_INVOCATION_STATUSES:
            return ExecutionDecision(status="invalid_state", invocation=invocation)
        if parent.failure_fingerprint is None:
            return ExecutionDecision(
                status="waiting_for_retry",
                invocation=invocation,
                error="parent failure fingerprint is missing",
            )
        try:
            retry_decision = self.retry_policy.evaluate(
                invocation=invocation,
                task=task,
                parent_attempt=parent,
                diagnosis=command.diagnosis,
                diagnostic_evidence_ids=command.diagnostic_evidence_ids,
                now=self.clock.now(),
            )
        except Exception as exc:
            return ExecutionDecision(
                status="waiting_for_retry",
                invocation=invocation,
                error=f"retry policy unavailable: {type(exc).__name__}",
            )
        if not isinstance(retry_decision, RetryPolicyDecision):
            return ExecutionDecision(
                status="waiting_for_retry",
                invocation=invocation,
                error="invalid retry policy decision",
            )
        if retry_decision.status != "allowed":
            return ExecutionDecision(
                status=(
                    "waiting_for_backoff"
                    if retry_decision.status == "waiting_for_backoff"
                    else "retry_denied"
                ),
                invocation=invocation,
                error=retry_decision.reason,
            )
        decisions, evaluation_error = self._permission_decisions(
            command=command,
            manifest=manifest,
            inputs=invocation.inputs,
        )
        permission_status, reason = self._permission_status(manifest, decisions)
        if evaluation_error is not None:
            permission_status, reason = "permission_denied", evaluation_error
        if permission_status != "allowed":
            return ExecutionDecision(status=permission_status, invocation=invocation, error=reason)
        invocation = replace(
            invocation,
            permission_decisions=decisions,
            approval_reference_hash=_secret_reference_hash(command.approval_reference),
        )
        if manifest.execution_mode == "sync":
            return self._run_sync(
                invocation,
                graph,
                task,
                manifest,
                parent=parent,
                retry=command,
                retry_decision=retry_decision,
            )
        return self._queue_async(
            invocation,
            graph,
            task,
            manifest,
            parent=parent,
            retry=command,
            retry_decision=retry_decision,
        )


__all__ = [
    "CapabilityRegistry",
    "Clock",
    "ContextValidation",
    "ContextValidator",
    "EvidenceGate",
    "EvidenceGateDecision",
    "EvidenceIntent",
    "EvidenceRecord",
    "ExecuteCapabilityCommand",
    "ExecutionAttemptIntent",
    "ExecutionAttemptRecord",
    "ExecutionDecision",
    "IDFactory",
    "InvocationIntent",
    "InvocationRecord",
    "InvocationRepository",
    "OutboxIntent",
    "OutboxRecord",
    "PermissionEvaluator",
    "PersistenceIntent",
    "ProvenanceIntent",
    "ProvenanceRecord",
    "RetryCapabilityCommand",
    "RetryPolicy",
    "RetryPolicyDecision",
    "ScientificTaskIntent",
    "ScientificTaskRecord",
    "SessionCapabilityExecutor",
    "SyncExecutor",
    "TaskGraphIntent",
    "TaskGraphRecord",
    "VerifiedEvidenceSpec",
    "VerifiedProvenanceSpec",
]
