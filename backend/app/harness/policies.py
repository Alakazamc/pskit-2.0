"""AI4S 生信 Harness 的可信策略适配层。

本模块只做持久事实的读取和 fail-closed 判定，不连接数据库、不执行科学工具，也不认识
``ResearchRun``。实际服务应把 Repository 查询封装成下列小型读取器后注入；这样策略可以在
数据库、审计回放和离线契约测试中使用同一套规则。
"""

from __future__ import annotations

# wzf：本适配层只用于已有持久化 Evidence 的恢复/回放核验；首次执行必须由执行收据流程预先登记 Evidence，不能由本层自举成功。
# Provenance 同样必须来自持久事实读取器，未完成生产接线时保持 fail-closed。

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Protocol, runtime_checkable

from app.harness.capabilities import CapabilityManifest
from app.harness.contracts import CapabilityResult, PermissionDecision
from app.harness.execution import (
    ContextValidation,
    EvidenceGateDecision,
    ExecutionAttemptRecord,
    InvocationRecord,
    RetryPolicyDecision,
    ScientificTaskRecord,
    VerifiedEvidenceSpec,
    VerifiedProvenanceSpec,
)


CONTEXT_POLICY_ID = "ai4s-context-policy-v1"
PERMISSION_POLICY_ID = "ai4s-permission-policy-v1"
RETRY_POLICY_ID = "ai4s-retry-policy-v1"
EVIDENCE_POLICY_ID = "ai4s-evidence-policy-v1"

_MISSING = object()
_ACTIVE_CONTEXT_STATUSES = frozenset(
    {
        "active",
        "draft",
        "planned",
        "running",
        "paused",
        "waiting_for_input",
        "waiting_for_resource",
        "waiting_for_dependency",
        "waiting_for_approval",
    }
)
_ACTIVE_SKILL_STATUSES = frozenset(
    {
        "planned",
        "waiting_for_approval",
        "running",
        "waiting_for_resource",
        "waiting_for_dependency",
        "waiting_for_input",
        "paused",
    }
)
_REJECTED_EVIDENCE_STATUSES = frozenset({"rejected", "superseded", "deleted"})
_TRUSTED_ROLES = frozenset({"admin", "scientist", "researcher", "operator", "reviewer"})
_DEFAULT_ROLE_PERMISSIONS: Mapping[str, frozenset[str]] = {
    "admin": frozenset({"*"}),
    "scientist": frozenset(
        {"session.read", "artifact.write", "compute.execute", "legacy.bridge", "mcp.invoke"}
    ),
    "researcher": frozenset(
        {"session.read", "artifact.write", "compute.execute", "legacy.bridge", "mcp.invoke"}
    ),
    "operator": frozenset(
        {"session.read", "artifact.write", "compute.execute", "legacy.bridge", "mcp.invoke"}
    ),
    "reviewer": frozenset({"session.read", "artifact.read"}),
}
_FORBIDDEN_SCIENTIFIC_CLAIMS = frozenset(
    {
        "experimental",
        "measured_affinity",
        "binding_affinity",
        "clinical",
        "patent",
        "validated_in_vivo",
    }
)


def _field(record: object, name: str, default: Any = None) -> Any:
    if record is None:
        return default
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def _text(value: object) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _same_id(left: object, right: object) -> bool:
    left_text = _text(left)
    right_text = _text(right)
    return left_text is not None and right_text is not None and left_text == right_text


def _as_sequence(value: object) -> tuple[Any, ...]:
    if value is None or isinstance(value, (str, bytes)):
        return () if value is None else (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    if isinstance(value, set | frozenset):
        return tuple(value)
    return (value,)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return value.value
    return value


def _digest(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def _safe_call(reader: object, method: str, *args: Any) -> Any:
    function = getattr(reader, method, None)
    if not callable(function):
        raise LookupError(f"fact reader does not implement {method}")
    return function(*args)


@runtime_checkable
class ContextFactsReader(Protocol):
    """提供 Session/User/Goal/Skill 及其关系的持久事实。"""

    def get_user(self, user_id: str) -> Mapping[str, Any] | None: ...

    def get_session(self, session_id: str) -> Mapping[str, Any] | None: ...

    def get_goal(self, goal_id: str) -> Mapping[str, Any] | None: ...

    def get_skill_execution(self, skill_execution_id: str) -> Mapping[str, Any] | None: ...

    def get_session_goal_link(self, session_id: str, goal_id: str) -> Mapping[str, Any] | None: ...

    def get_turn(self, turn_id: str) -> Mapping[str, Any] | None: ...


class PersistentContextValidator:
    """根据持久化身份和归属关系验证一次 Session 原生调用。"""

    def __init__(self, reader: ContextFactsReader, *, policy_id: str = CONTEXT_POLICY_ID) -> None:
        self.reader = reader
        self.policy_id = policy_id

    def validate_execution(
        self,
        *,
        user_id: str,
        session_id: str,
        turn_id: str | None,
        goal_id: str | None,
        skill_execution_id: str | None,
    ) -> ContextValidation:
        try:
            user = _safe_call(self.reader, "get_user", user_id)
            session = _safe_call(self.reader, "get_session", session_id)
            if user is None or session is None:
                return ContextValidation(False, "用户或科研会话不存在", self.policy_id)
            if not _same_id(_field(user, "id", user_id), user_id):
                return ContextValidation(False, "用户身份事实不一致", self.policy_id)
            if not _same_id(_field(session, "id", session_id), session_id):
                return ContextValidation(False, "会话身份事实不一致", self.policy_id)
            if not _same_id(_field(session, "user_id"), user_id):
                return ContextValidation(False, "会话不属于当前用户", self.policy_id)
            if _text(_field(user, "status", "active")) not in {"active", "enabled"}:
                return ContextValidation(False, "用户当前不可执行科研能力", self.policy_id)
            if _text(_field(session, "status", "active")) not in {"active", "draft", "paused"}:
                return ContextValidation(False, "会话当前不可执行科研能力", self.policy_id)

            if turn_id is not None:
                turn = _safe_call(self.reader, "get_turn", turn_id)
                if turn is None or not _same_id(_field(turn, "id", turn_id), turn_id):
                    return ContextValidation(False, "交互轮次不存在", self.policy_id)
                if not _same_id(_field(turn, "user_id"), user_id) or not _same_id(
                    _field(turn, "session_id"), session_id
                ):
                    return ContextValidation(False, "交互轮次不属于当前用户会话", self.policy_id)
                if _text(_field(turn, "status", "queued")) not in {
                    "queued",
                    "running",
                    "waiting_for_input",
                }:
                    return ContextValidation(False, "交互轮次当前不可执行科研能力", self.policy_id)

            goal = None
            if goal_id is not None:
                goal = _safe_call(self.reader, "get_goal", goal_id)
                link = _safe_call(self.reader, "get_session_goal_link", session_id, goal_id)
                if goal is None or link is None:
                    return ContextValidation(False, "目标或会话目标关系不存在", self.policy_id)
                if not _same_id(_field(goal, "id", goal_id), goal_id) or not _same_id(
                    _field(link, "goal_id"), goal_id
                ):
                    return ContextValidation(False, "目标身份事实不一致", self.policy_id)
                if not _same_id(_field(goal, "user_id"), user_id) or not _same_id(
                    _field(link, "user_id"), user_id
                ):
                    return ContextValidation(False, "目标不属于当前用户", self.policy_id)
                if _text(_field(goal, "status", "active")) not in _ACTIVE_CONTEXT_STATUSES:
                    return ContextValidation(False, "目标当前不可执行科研能力", self.policy_id)

            if skill_execution_id is not None:
                skill = _safe_call(self.reader, "get_skill_execution", skill_execution_id)
                if skill is None or not _same_id(
                    _field(skill, "id", skill_execution_id), skill_execution_id
                ):
                    return ContextValidation(False, "Skill 执行不存在", self.policy_id)
                if not _same_id(_field(skill, "user_id"), user_id):
                    return ContextValidation(False, "Skill 执行不属于当前用户", self.policy_id)
                skill_goal_id = _text(_field(skill, "goal_id"))
                if skill_goal_id is None:
                    return ContextValidation(False, "Skill 执行缺少持久化目标归属", self.policy_id)
                if goal is not None and not _same_id(skill_goal_id, goal_id):
                    return ContextValidation(False, "Skill 执行与目标不一致", self.policy_id)
                if goal is None:
                    goal = _safe_call(self.reader, "get_goal", skill_goal_id)
                    link = _safe_call(
                        self.reader, "get_session_goal_link", session_id, skill_goal_id
                    )
                    if (
                        goal is None
                        or link is None
                        or not _same_id(_field(goal, "user_id"), user_id)
                        or not _same_id(_field(link, "user_id"), user_id)
                    ):
                        return ContextValidation(
                            False, "Skill 执行的目标不属于当前会话", self.policy_id
                        )
                if _text(_field(skill, "status", "planned")) not in _ACTIVE_SKILL_STATUSES:
                    return ContextValidation(False, "Skill 执行当前不可继续", self.policy_id)
            return ContextValidation(True, "Session、User、Goal、Skill 归属已核验", self.policy_id)
        except Exception as exc:
            return ContextValidation(
                False, f"上下文事实读取失败: {type(exc).__name__}", self.policy_id
            )


@runtime_checkable
class PermissionFactsReader(Protocol):
    """提供用户角色、外部依赖授权和审批事实；不得接受模型自报授权。"""

    def get_user(self, user_id: str) -> Mapping[str, Any] | None: ...

    def get_external_authorization(
        self, user_id: str, dependency: str
    ) -> Mapping[str, Any] | None: ...

    def get_approval(self, reference_hash: str) -> Mapping[str, Any] | None: ...


class PersistentPermissionEvaluator:
    """依据受信角色、风险/资源、外部授权和持久化审批事实做权限判定。"""

    def __init__(
        self,
        reader: PermissionFactsReader,
        *,
        policy_id: str = PERMISSION_POLICY_ID,
        trusted_roles: frozenset[str] = _TRUSTED_ROLES,
    ) -> None:
        self.reader = reader
        self.policy_id = policy_id
        self.trusted_roles = frozenset(trusted_roles)

    @staticmethod
    def _approval_hash(reference: str) -> str:
        # 与 execution.py 的 approval_reference_hash 持久化格式保持一致：只保存裸 SHA-256。
        return sha256(reference.encode("utf-8")).hexdigest()

    def _user_permissions(self, user: object) -> tuple[set[str], bool]:
        status = _text(_field(user, "status", "active"))
        raw_roles = _field(user, "roles", _field(user, "trusted_roles", ()))
        roles = {str(role).strip() for role in _as_sequence(raw_roles) if str(role).strip()}
        trusted = bool(_field(user, "trusted", _field(user, "is_trusted", False)))
        trusted = trusted and bool(roles & self.trusted_roles)
        permissions = {
            str(item).strip()
            for item in _as_sequence(_field(user, "permissions", ()))
            if str(item).strip()
        }
        role_permissions = _field(user, "role_permissions", {})
        if isinstance(role_permissions, Mapping):
            for role in roles:
                permissions.update(
                    str(item).strip()
                    for item in _as_sequence(role_permissions.get(role, ()))
                    if str(item).strip()
                )
        for role in roles & self.trusted_roles:
            permissions.update(_DEFAULT_ROLE_PERMISSIONS.get(role, ()))
        return permissions, trusted and status in {"active", "enabled"}

    @staticmethod
    def _external_dependencies(manifest: CapabilityManifest) -> tuple[str, ...]:
        profile = manifest.dependency_profile
        raw = profile.get("dependencies", ()) if isinstance(profile, Mapping) else ()
        return tuple(str(item) for item in _as_sequence(raw))

    def _approval_allowed(
        self,
        *,
        user_id: str,
        session_id: str,
        manifest: CapabilityManifest,
        approval_reference: str | None,
    ) -> bool:
        if not approval_reference:
            return False
        reference_hash = self._approval_hash(approval_reference)
        approval = _safe_call(self.reader, "get_approval", reference_hash)
        if approval is None:
            return False
        if _text(_field(approval, "reference_hash")) != reference_hash:
            return False
        if _text(_field(approval, "status")) not in {"approved", "active"}:
            return False
        if not _same_id(_field(approval, "user_id"), user_id):
            return False
        approval_session = _field(approval, "session_id")
        if approval_session is not None and not _same_id(approval_session, session_id):
            return False
        capability = _field(approval, "capability_id")
        return capability is None or _same_id(capability, manifest.capability_id)

    def evaluate(
        self,
        *,
        user_id: str,
        session_id: str,
        manifest: CapabilityManifest,
        inputs: Mapping[str, Any],
        approval_reference: str | None,
    ) -> tuple[PermissionDecision, ...]:
        del inputs
        try:
            user = _safe_call(self.reader, "get_user", user_id)
            if user is None:
                return tuple(
                    PermissionDecision(permission, reason="用户不存在", policy_id=self.policy_id)
                    for permission in manifest.required_permissions
                )
            permissions, trusted = self._user_permissions(user)
            if not trusted:
                return tuple(
                    PermissionDecision(
                        permission, reason="用户角色未通过受信校验", policy_id=self.policy_id
                    )
                    for permission in manifest.required_permissions
                )

            resource_profile = manifest.resource_profile
            cost_tier = (
                _text(resource_profile.get("cost_tier"))
                if isinstance(resource_profile, Mapping)
                else None
            )
            high_cost = (
                manifest.risk_level in {"high_cost", "destructive"}
                or manifest.approval_required
                or "policy.approval" in manifest.required_permissions
                or cost_tier in {"high", "critical"}
            )
            external_required = manifest.capability_type == "external_api" or any(
                dependency.endswith(("_mcp", "_api"))
                or dependency in {"serpapi", "rcsb_files", "rcsb_pdb_api"}
                for dependency in self._external_dependencies(manifest)
            )
            external_ok = True
            if external_required:
                for dependency in self._external_dependencies(manifest):
                    authorization = _safe_call(
                        self.reader, "get_external_authorization", user_id, dependency
                    )
                    if authorization is None or not bool(_field(authorization, "allowed", False)):
                        external_ok = False
                        break

            approval_ok = (
                self._approval_allowed(
                    user_id=user_id,
                    session_id=session_id,
                    manifest=manifest,
                    approval_reference=approval_reference,
                )
                if high_cost
                else True
            )
            decisions: list[PermissionDecision] = []
            for permission in manifest.required_permissions:
                if permission == "policy.approval":
                    if not approval_ok:
                        decisions.append(
                            PermissionDecision(
                                permission,
                                reason="缺少持久化审批事实",
                                approval_required=True,
                                policy_id=self.policy_id,
                            )
                        )
                    else:
                        decisions.append(
                            PermissionDecision(permission, granted=True, policy_id=self.policy_id)
                        )
                    continue
                if permission not in permissions and "*" not in permissions:
                    decisions.append(
                        PermissionDecision(
                            permission, reason="受信角色未授予所需权限", policy_id=self.policy_id
                        )
                    )
                elif permission == "compute.execute" and not external_ok:
                    decisions.append(
                        PermissionDecision(
                            permission, reason="外部科学依赖授权缺失", policy_id=self.policy_id
                        )
                    )
                elif not external_ok:
                    decisions.append(
                        PermissionDecision(
                            permission, reason="外部科学依赖授权缺失", policy_id=self.policy_id
                        )
                    )
                else:
                    decisions.append(
                        PermissionDecision(permission, granted=True, policy_id=self.policy_id)
                    )
            return tuple(decisions)
        except Exception as exc:
            return tuple(
                PermissionDecision(
                    permission,
                    reason=f"权限事实读取失败: {type(exc).__name__}",
                    policy_id=self.policy_id,
                )
                for permission in manifest.required_permissions
            )


@runtime_checkable
class RetryFactsReader(Protocol):
    """提供诊断 Evidence 与当前资源/依赖快照。"""

    def get_evidence(self, evidence_id: str) -> Mapping[str, Any] | None: ...

    def get_resource_snapshot(self, scientific_task_id: str) -> Mapping[str, Any] | None: ...

    def get_dependency_snapshot(self, scientific_task_id: str) -> Mapping[str, Any] | None: ...


class PersistentRetryPolicy:
    """无限重试但不高速重放；只有持久诊断且条件改变才允许创建新 Attempt。"""

    def __init__(
        self,
        reader: RetryFactsReader,
        *,
        policy_id: str = RETRY_POLICY_ID,
        base_backoff_seconds: int = 5,
    ) -> None:
        self.reader = reader
        self.policy_id = policy_id
        self.base_backoff_seconds = max(1, int(base_backoff_seconds))

    @staticmethod
    def _snapshot(record: object, key: str) -> Any:
        direct = _field(record, key, _MISSING)
        if direct is not _MISSING:
            return direct
        metadata = _field(record, "metadata", _field(record, "metadata_json", {}))
        if isinstance(metadata, Mapping):
            return metadata.get(key, _MISSING)
        output = _field(record, "output", {})
        if isinstance(output, Mapping):
            return output.get(key, _MISSING)
        return _MISSING

    def evaluate(
        self,
        *,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        parent_attempt: ExecutionAttemptRecord,
        diagnosis: str,
        diagnostic_evidence_ids: tuple[str, ...],
        now: datetime,
    ) -> RetryPolicyDecision:
        try:
            ids = tuple(str(item) for item in diagnostic_evidence_ids)
            if not ids or len(set(ids)) != len(ids):
                return RetryPolicyDecision(
                    "denied", "必须提供去重后的持久化诊断 Evidence ID", self.policy_id
                )
            parent_ids = tuple(str(item) for item in parent_attempt.diagnostic_evidence_ids)
            if parent_ids and set(ids).issubset(parent_ids):
                return self._waiting(parent_attempt, now, "没有新的诊断证据，拒绝相同条件高速重放")
            evidence: list[Mapping[str, Any]] = []
            for evidence_id in ids:
                item = _safe_call(self.reader, "get_evidence", evidence_id)
                if item is None:
                    return RetryPolicyDecision("denied", "诊断 Evidence 不存在", self.policy_id)
                if _text(_field(item, "status")) in _REJECTED_EVIDENCE_STATUSES or not bool(
                    _field(item, "sufficient", False)
                ):
                    return RetryPolicyDecision(
                        "denied", "诊断 Evidence 未达到可重试状态", self.policy_id
                    )
                if not _same_id(_field(item, "user_id"), invocation.user_id) or not _same_id(
                    _field(item, "scientific_task_id"), task.id
                ):
                    return RetryPolicyDecision(
                        "denied", "诊断 Evidence 所有权不一致", self.policy_id
                    )
                if not _same_id(_field(item, "attempt_id"), parent_attempt.id):
                    return RetryPolicyDecision(
                        "denied", "诊断 Evidence 未绑定失败 Attempt", self.policy_id
                    )
                if not _text(_field(item, "content_hash")):
                    return RetryPolicyDecision(
                        "denied", "诊断 Evidence 缺少内容哈希", self.policy_id
                    )
                evidence.append(item)
            current_resource = _safe_call(self.reader, "get_resource_snapshot", task.id)
            current_dependency = _safe_call(self.reader, "get_dependency_snapshot", task.id)
            if current_resource is None or current_dependency is None:
                return RetryPolicyDecision("denied", "当前资源或依赖快照不可用", self.policy_id)
            parent_fingerprint = _text(_field(parent_attempt, "failure_fingerprint"))
            if parent_fingerprint is None:
                return RetryPolicyDecision("denied", "失败 Attempt 缺少失败指纹", self.policy_id)
            changed = False
            for item in evidence:
                fingerprint = _text(
                    _field(item, "failure_fingerprint", _field(item, "failure_signature"))
                )
                if fingerprint is None:
                    return RetryPolicyDecision(
                        "denied", "诊断 Evidence 缺少失败指纹", self.policy_id
                    )
                if fingerprint != parent_fingerprint:
                    return RetryPolicyDecision(
                        "denied", "诊断 Evidence 与父 Attempt 失败指纹不一致", self.policy_id
                    )
                resource = _field(item, "resource_snapshot", _field(item, "resource"))
                dependency = _field(item, "dependency_snapshot", _field(item, "dependencies"))
                if not isinstance(resource, Mapping) or not isinstance(dependency, Mapping):
                    return RetryPolicyDecision(
                        "denied", "诊断 Evidence 缺少资源或依赖快照", self.policy_id
                    )
            if not isinstance(current_resource, Mapping) or not isinstance(
                current_dependency, Mapping
            ):
                return RetryPolicyDecision("denied", "当前资源或依赖快照格式无效", self.policy_id)
            for item in evidence:
                resource = _field(item, "resource_snapshot", _field(item, "resource"))
                dependency = _field(item, "dependency_snapshot", _field(item, "dependencies"))
                if _digest(resource) != _digest(current_resource) or _digest(dependency) != _digest(
                    current_dependency
                ):
                    changed = True
            snapshot_hash = _digest(
                {
                    "evidence": [
                        (str(_field(item, "id")), _field(item, "content_hash")) for item in evidence
                    ],
                    "resource": current_resource,
                    "dependency": current_dependency,
                    "diagnosis": diagnosis,
                }
            )
            if not changed:
                return self._waiting(
                    parent_attempt, now, "失败指纹、资源和依赖均未改变，等待新的可操作条件"
                )
            return RetryPolicyDecision(
                "allowed",
                "诊断证据证明失败条件已改变，可创建新的 Attempt",
                self.policy_id,
                snapshot_hash,
            )
        except Exception as exc:
            return RetryPolicyDecision(
                "denied", f"重试事实读取失败: {type(exc).__name__}", self.policy_id
            )

    def _waiting(
        self, parent_attempt: ExecutionAttemptRecord, now: datetime, reason: str
    ) -> RetryPolicyDecision:
        delay = min(
            self.base_backoff_seconds * (2 ** min(max(parent_attempt.attempt_no - 1, 0), 8)), 900
        )
        return RetryPolicyDecision(
            "waiting_for_backoff",
            reason,
            self.policy_id,
            available_at=now + timedelta(seconds=delay),
        )


@runtime_checkable
class EvidenceFactsReader(Protocol):
    """提供注册 Artifact 与 Evidence 的完整持久事实。"""

    def get_artifact(self, artifact_id: str) -> Mapping[str, Any] | None: ...

    def get_evidence(self, evidence_id: str) -> Mapping[str, Any] | None: ...

    def get_provenance(self, source_id: str) -> Mapping[str, Any] | None: ...


class PersistentEvidenceGate:
    """核验 Artifact/Evidence/Attempt/CompletionContract 后才允许写入成功结果。"""

    def __init__(self, reader: EvidenceFactsReader, *, policy_id: str = EVIDENCE_POLICY_ID) -> None:
        self.reader = reader
        self.policy_id = policy_id

    @staticmethod
    def _hash_for_artifact(result: CapabilityResult, artifact_id: str, count: int) -> str | None:
        hashes = result.hashes
        return (
            hashes.get(artifact_id)
            or hashes.get(f"artifact:{artifact_id}")
            or (hashes.get("output") if count == 1 else None)
        )

    @staticmethod
    def _output_has(output: Mapping[str, Any], field_path: str) -> bool:
        current: Any = output
        for part in field_path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return False
            current = current[part]
        return True

    def verify(
        self,
        *,
        manifest: CapabilityManifest,
        result: CapabilityResult,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
    ) -> EvidenceGateDecision:
        try:
            contract = manifest.completion_contract
            evidence_contract = manifest.evidence_contract
            if not result.evidence:
                return EvidenceGateDecision(
                    False, "结果必须引用预存持久化 Evidence，不允许首次调用自举成功", self.policy_id
                )
            if contract.requires_terminal_status and not result.terminal:
                return EvidenceGateDecision(False, "结果尚未进入终态", self.policy_id)
            if result.status.value not in contract.accepted_statuses:
                return EvidenceGateDecision(
                    False, "结果状态不满足 CompletionContract", self.policy_id
                )
            if result.attempt_id != attempt.id:
                return EvidenceGateDecision(False, "结果未绑定当前 Attempt", self.policy_id)
            if contract.requires_structured_output and not isinstance(result.output, Mapping):
                return EvidenceGateDecision(False, "结果缺少结构化输出", self.policy_id)
            if any(
                not self._output_has(result.output, field)
                for field in contract.required_output_fields
            ):
                return EvidenceGateDecision(
                    False, "结果缺少 CompletionContract 要求的输出字段", self.policy_id
                )
            if (
                len(result.artifacts) < contract.minimum_artifact_count
                or len(result.artifacts) < evidence_contract.minimum_artifact_count
            ):
                return EvidenceGateDecision(False, "结果产物数量不足", self.policy_id)
            if any(required not in result.artifacts for required in contract.required_artifacts):
                return EvidenceGateDecision(
                    False, "结果缺少 CompletionContract 要求的产物", self.policy_id
                )
            if evidence_contract.require_provenance and not result.provenance:
                return EvidenceGateDecision(False, "结果缺少来源关系", self.policy_id)
            if evidence_contract.require_hash and not result.hashes:
                return EvidenceGateDecision(False, "结果缺少内容哈希", self.policy_id)
            if not _same_id(invocation.user_id, task.user_id) or not _same_id(
                attempt.user_id, invocation.user_id
            ):
                return EvidenceGateDecision(False, "执行对象所有权不一致", self.policy_id)

            verified_artifacts: list[Mapping[str, Any]] = []
            for artifact_id in result.artifacts:
                artifact = _safe_call(self.reader, "get_artifact", artifact_id)
                if artifact is None:
                    return EvidenceGateDecision(False, "产物未登记", self.policy_id)
                if not _same_id(_field(artifact, "id", artifact_id), artifact_id) or not _same_id(
                    _field(artifact, "user_id"), invocation.user_id
                ):
                    return EvidenceGateDecision(False, "产物所有权不一致", self.policy_id)
                if not _same_id(_field(artifact, "attempt_id"), attempt.id) or not _same_id(
                    _field(artifact, "scientific_task_id"), task.id
                ):
                    return EvidenceGateDecision(
                        False, "产物未绑定当前 Attempt/Task", self.policy_id
                    )
                if _text(_field(artifact, "status", "registered")) in _REJECTED_EVIDENCE_STATUSES:
                    return EvidenceGateDecision(False, "产物已被拒绝或删除", self.policy_id)
                actual_hash = _text(_field(artifact, "content_hash", _field(artifact, "sha256")))
                expected_hash = self._hash_for_artifact(result, artifact_id, len(result.artifacts))
                if not actual_hash or not expected_hash or actual_hash != expected_hash:
                    return EvidenceGateDecision(False, "产物内容哈希缺失或不匹配", self.policy_id)
                verified_artifacts.append(artifact)

            records: list[Mapping[str, Any]] = []
            for evidence_id in result.evidence:
                record = _safe_call(self.reader, "get_evidence", evidence_id)
                if record is None:
                    return EvidenceGateDecision(
                        False, "结果引用的持久化 Evidence 不存在", self.policy_id
                    )
                records.append(record)
            if len(records) < evidence_contract.minimum_evidence_count:
                return EvidenceGateDecision(False, "持久化 Evidence 数量不足", self.policy_id)
            required_types = set(evidence_contract.required_evidence_types)
            available_types = {_text(_field(record, "evidence_type")) for record in records}
            if not required_types.issubset(available_types):
                return EvidenceGateDecision(
                    False, "缺少 Evidence contract 要求的证据类型", self.policy_id
                )

            verified_artifact_ids = set(result.artifacts)
            artifacts_required = (
                bool(result.artifacts)
                or contract.minimum_artifact_count > 0
                or evidence_contract.minimum_artifact_count > 0
                or bool(contract.required_artifacts)
                or bool(evidence_contract.required_artifacts)
            )
            verified_evidence: list[VerifiedEvidenceSpec] = []
            for record in records:
                if _text(_field(record, "status")) in _REJECTED_EVIDENCE_STATUSES or not bool(
                    _field(record, "sufficient", False)
                ):
                    return EvidenceGateDecision(False, "Evidence 未达到可接受状态", self.policy_id)
                if (
                    not _same_id(_field(record, "user_id"), invocation.user_id)
                    or not _same_id(_field(record, "scientific_task_id"), task.id)
                    or not _same_id(_field(record, "attempt_id"), attempt.id)
                ):
                    return EvidenceGateDecision(
                        False, "Evidence 所有权或 Attempt 绑定不一致", self.policy_id
                    )
                content_hash = _text(_field(record, "content_hash"))
                statement = _text(_field(record, "statement", _field(record, "title")))
                evidence_type = _text(_field(record, "evidence_type"))
                if not content_hash or not statement or not evidence_type:
                    return EvidenceGateDecision(
                        False, "Evidence 缺少类型、陈述或内容哈希", self.policy_id
                    )
                if evidence_contract.reviewer_required and not _text(
                    _field(record, "reviewer_id", _field(record, "reviewer"))
                ):
                    return EvidenceGateDecision(False, "Evidence 缺少要求的审查人", self.policy_id)
                metadata = _field(record, "metadata", _field(record, "metadata_json", {}))
                if not isinstance(metadata, Mapping):
                    return EvidenceGateDecision(False, "Evidence metadata 格式无效", self.policy_id)
                raw_artifact_ids = _field(record, "artifact_ids", _MISSING)
                if raw_artifact_ids is _MISSING:
                    raw_artifact_ids = _field(record, "artifact_id", _MISSING)
                if raw_artifact_ids is _MISSING:
                    raw_artifact_ids = metadata.get(
                        "artifact_ids", metadata.get("artifact_id", _MISSING)
                    )
                artifact_ids = (
                    tuple(_text(item) for item in _as_sequence(raw_artifact_ids) if _text(item))
                    if raw_artifact_ids is not _MISSING
                    else ()
                )
                if not artifact_ids and artifacts_required:
                    return EvidenceGateDecision(
                        False, "当前契约要求 Evidence 显式引用非空 artifact_ids", self.policy_id
                    )
                if len(set(artifact_ids)) != len(artifact_ids):
                    return EvidenceGateDecision(
                        False, "Evidence artifact_ids 不得重复", self.policy_id
                    )
                if any(item not in verified_artifact_ids for item in artifact_ids):
                    return EvidenceGateDecision(
                        False, "Evidence 引用的 Artifact 未通过当前结果核验", self.policy_id
                    )
                verified_evidence.append(
                    VerifiedEvidenceSpec(
                        evidence_type,
                        statement,
                        artifact_ids,
                        _field(record, "source_uri"),
                        content_hash,
                        _field(record, "reviewer_id", _field(record, "reviewer")),
                        metadata,
                    )
                )

            metadata = result.metadata
            claimed_scope = (
                metadata.get("claims_scope", ()) if isinstance(metadata, Mapping) else ()
            )
            allowed_scope = set(evidence_contract.claims_scope)
            if any(str(scope) not in allowed_scope for scope in _as_sequence(claimed_scope)):
                return EvidenceGateDecision(
                    False, "科学主张超出 Evidence contract 范围", self.policy_id
                )
            if isinstance(metadata, Mapping) and any(
                bool(metadata.get(claim)) for claim in _FORBIDDEN_SCIENTIFIC_CLAIMS
            ):
                return EvidenceGateDecision(
                    False, "计算 Evidence 不得冒充实验或临床结论", self.policy_id
                )
            verified_provenance: list[VerifiedProvenanceSpec] = []
            for source in result.provenance:
                record = _safe_call(self.reader, "get_provenance", source)
                if record is None:
                    return EvidenceGateDecision(
                        False, "结果引用的 Provenance 未持久化", self.policy_id
                    )
                source_id = _text(_field(record, "source_id"))
                if source_id is None or not _same_id(source_id, source):
                    return EvidenceGateDecision(
                        False, "Provenance source_id 与结果引用不一致", self.policy_id
                    )
                if not _same_id(_field(record, "user_id"), invocation.user_id):
                    return EvidenceGateDecision(False, "Provenance 所有权不一致", self.policy_id)
                if _text(_field(record, "target_type")) != "execution_attempt" or not _same_id(
                    _field(record, "target_id"), attempt.id
                ):
                    return EvidenceGateDecision(
                        False, "Provenance 未绑定当前 execution_attempt", self.policy_id
                    )
                source_type = _text(_field(record, "source_type"))
                relation_type = _text(_field(record, "relation_type"))
                edge_id = _text(_field(record, "id"))
                metadata = _field(record, "metadata", _field(record, "metadata_json", {}))
                if (
                    source_type is None
                    or relation_type is None
                    or edge_id is None
                    or not isinstance(metadata, Mapping)
                ):
                    return EvidenceGateDecision(
                        False, "Provenance 缺少类型、关系或 metadata", self.policy_id
                    )
                metadata = {**metadata, "provenance_edge_id": edge_id}
                verified_provenance.append(
                    VerifiedProvenanceSpec(source_type, source_id, relation_type, metadata)
                )
            provenance = tuple(verified_provenance)
            if evidence_contract.require_provenance and not provenance:
                return EvidenceGateDecision(False, "来源关系核验失败", self.policy_id)
            return EvidenceGateDecision(
                True,
                "Artifact、Evidence、CompletionContract 和科学边界均已核验",
                self.policy_id,
                tuple(verified_evidence),
                provenance,
            )
        except Exception as exc:
            return EvidenceGateDecision(
                False, f"证据事实读取失败: {type(exc).__name__}", self.policy_id
            )


# 生产命名别名：保留 Persistent 名称以便审计，也允许服务注册层使用 Production 名称。
ProductionContextValidator = PersistentContextValidator
ProductionPermissionEvaluator = PersistentPermissionEvaluator
ProductionRetryPolicy = PersistentRetryPolicy
PrePersistedEvidenceGate = PersistentEvidenceGate


__all__ = [
    "CONTEXT_POLICY_ID",
    "EVIDENCE_POLICY_ID",
    "PERMISSION_POLICY_ID",
    "RETRY_POLICY_ID",
    "ContextFactsReader",
    "EvidenceFactsReader",
    "PermissionFactsReader",
    "RetryFactsReader",
    "PersistentContextValidator",
    "PersistentEvidenceGate",
    "PersistentPermissionEvaluator",
    "PersistentRetryPolicy",
    "PrePersistedEvidenceGate",
    "ProductionContextValidator",
    "ProductionPermissionEvaluator",
    "ProductionRetryPolicy",
]
