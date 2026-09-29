"""首次同步能力执行的受信收据运行时。

``policies.PrePersistedEvidenceGate`` 只适合恢复/回放已经落库的 Evidence。
本模块提供相反的生产路径：Worker 先返回绑定当前上下文的 Receipt，外部注入的
ReceiptVerifier 负责真实性判定，随后本门只把已核验草稿转换为执行层的
``VerifiedEvidenceSpec``/``VerifiedProvenanceSpec``。本模块不保存 verifier 密钥，
也不连接数据库；产物的登记真实性必须由 verifier/受信执行器共同证明。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from app.harness.capabilities import CapabilityManifest
from app.harness.contracts import (
    ArtifactReceipt,
    CapabilityResult,
    ExecutionReceipt,
    canonical_json_hash,
)
from app.harness.execution import (
    EvidenceGateDecision,
    ExecutionAttemptRecord,
    InvocationRecord,
    ScientificTaskRecord,
    VerifiedEvidenceSpec,
    VerifiedProvenanceSpec,
)


# 收据构造与验证使用同一确定性内容寻址算法；从 runtime 重新导出便于 Worker 适配器复用。
canonical_hash = canonical_json_hash


DEFAULT_RECEIPT_ISSUER = "pskit.sync-executor"
RECEIPT_EVIDENCE_POLICY_ID = "receipt-evidence-gate-v1"
_TERMINAL_SUCCESS = "succeeded"
_REJECTED_ARTIFACT_STATUSES = frozenset({"rejected", "deleted", "invalid"})
_FORBIDDEN_SCIENTIFIC_CLAIMS = frozenset(
    {
        "experimental_affinity",
        "experimental_specificity",
        "clinical_efficacy",
        "patentability",
        "broad_spectrum_performance",
    }
)


class ReceiptVerifier(Protocol):
    """只负责 Receipt authenticity proof/signature 的真实性判定。

    verifier 可以在进程外调用受控密钥服务；密钥不能进入 Receipt、结果摘要或
    本模块状态。返回值必须是 ``bool`` 或 ``ReceiptVerification``，其他值一律拒绝。
    """

    def verify(
        self,
        *,
        receipt: ExecutionReceipt,
        manifest: CapabilityManifest,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
        result: CapabilityResult,
    ) -> bool | "ReceiptVerification": ...


@dataclass(frozen=True, slots=True)
class ReceiptVerification:
    """verifier 的显式判定结果。"""

    valid: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise TypeError("ReceiptVerification.valid must be a bool")
        if not isinstance(self.reason, str):
            raise TypeError("ReceiptVerification.reason must be a string")


def _text(value: object, field_name: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _same(left: object, right: object) -> bool:
    left_text = _text(left, "id")
    right_text = _text(right, "id")
    return left_text is not None and left_text == right_text


def _output_has(output: Mapping[str, Any], path: str) -> bool:
    current: Any = output
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


def _hash_matches(receipt_hash: str, expected: str) -> bool:
    """接受裸 sha256 与 ``sha256:`` 前缀两种历史持久格式。"""

    return receipt_hash.lower().removeprefix("sha256:") == expected.lower().removeprefix("sha256:")


class ReceiptEvidenceGate:
    """生产侧首次执行证据门，缺失/异常/False 均 fail-closed。"""

    # SessionCapabilityExecutor 可据此区分需要收据的新生产路径与旧回放适配器。
    requires_receipt = True

    def __init__(
        self,
        verifier: ReceiptVerifier,
        *,
        expected_issuer: str = DEFAULT_RECEIPT_ISSUER,
        issuer: str | None = None,
        policy_id: str = RECEIPT_EVIDENCE_POLICY_ID,
    ) -> None:
        if issuer is not None:
            expected_issuer = issuer
        expected = _text(expected_issuer, "expected_issuer")
        if expected is None:
            raise ValueError("expected_issuer must be non-empty")
        policy = _text(policy_id, "policy_id")
        if policy is None:
            raise ValueError("policy_id must be non-empty")
        self.verifier = verifier
        self.expected_issuer = expected
        self.policy_id = policy

    def _deny(self, reason: str) -> EvidenceGateDecision:
        return EvidenceGateDecision(False, reason, self.policy_id)

    def _authentic(
        self,
        *,
        receipt: ExecutionReceipt,
        manifest: CapabilityManifest,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
        result: CapabilityResult,
    ) -> tuple[bool, str | None]:
        verifier_method = getattr(self.verifier, "verify", None)
        if not callable(verifier_method):
            return False, "ReceiptVerifier 未提供 verify 判定"
        try:
            verdict = verifier_method(
                receipt=receipt,
                manifest=manifest,
                invocation=invocation,
                task=task,
                attempt=attempt,
                result=result,
            )
        except Exception as exc:
            return False, f"ReceiptVerifier 异常: {type(exc).__name__}"
        if isinstance(verdict, ReceiptVerification):
            return verdict.valid, verdict.reason or "ReceiptVerifier 拒绝收据"
        if isinstance(verdict, bool):
            return verdict, None if verdict else "ReceiptVerifier 拒绝收据"
        return False, "ReceiptVerifier 返回了无效判定"

    def _validate_identity(
        self,
        *,
        receipt: ExecutionReceipt,
        manifest: CapabilityManifest,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
        result: CapabilityResult,
    ) -> str | None:
        if receipt.issuer != self.expected_issuer:
            return "收据 issuer 不属于受信同步执行器"
        if not _same(receipt.user_id, invocation.user_id) or not _same(
            task.user_id, invocation.user_id
        ):
            return "收据/Task/Invocation user ownership 不一致"
        if not _same(receipt.session_id, invocation.session_id):
            return "收据 session ownership 不一致"
        if not _same(receipt.invocation_id, invocation.id):
            return "收据未绑定当前 Invocation"
        if not _same(receipt.task_id, task.id) or not _same(receipt.attempt_id, attempt.id):
            return "收据未绑定当前 Task/Attempt"
        if not _same(invocation.execution_attempt_id, attempt.id):
            return "Invocation 未绑定当前 Attempt"
        if task.task_type != manifest.capability_id:
            return "ScientificTask 类型与 Manifest 不一致"
        if task.idempotency_key != invocation.idempotency_key:
            return "Task/Invocation 幂等身份不一致"
        if canonical_json_hash(task.inputs) != canonical_json_hash(invocation.inputs):
            return "Task 输入与 Invocation 输入不一致"
        if canonical_json_hash(attempt.inputs) != canonical_json_hash(invocation.inputs):
            return "Attempt 输入与 Invocation 输入不一致"
        if (
            receipt.capability_id != manifest.capability_id
            or receipt.capability_id != invocation.capability_id
        ):
            return "收据 capability_id 不一致"
        if (
            receipt.capability_version != manifest.version
            or receipt.capability_version != invocation.capability_version
        ):
            return "收据 capability_version 不一致"
        if (
            receipt.manifest_digest != manifest.digest
            or receipt.manifest_digest != invocation.manifest_digest
        ):
            return "收据 manifest digest 不一致"
        if result.capability_id != receipt.capability_id:
            return "结果 capability_id 与收据不一致"
        if result.attempt_id != attempt.id:
            return "结果未绑定当前 Attempt"
        if result.status.value != receipt.result_status:
            return "收据 result status 与结果不一致"
        if receipt.result_status != _TERMINAL_SUCCESS:
            return "首次 ReceiptEvidenceGate 只接受 succeeded 收据"
        if not _hash_matches(receipt.input_hash, canonical_json_hash(invocation.inputs)):
            return "收据 input hash 与冻结输入不一致"
        if not _hash_matches(receipt.output_hash, canonical_json_hash(result.output)):
            return "收据 output hash 与结构化结果不一致"
        if result.hashes:
            output_hash = result.hashes.get("output")
            if output_hash is not None and not _hash_matches(str(output_hash), receipt.output_hash):
                return "结果 output hash 与收据不一致"
        receipt_artifact_ids = tuple(item.artifact_id for item in receipt.artifacts)
        if len(result.artifacts) != len(set(result.artifacts)):
            return "结果 artifact 集合含重复 ID"
        if set(result.artifacts) != set(receipt_artifact_ids):
            return "结果 artifact 集合与收据不一致"
        if result.evidence:
            evidence_types = tuple(item.evidence_type for item in receipt.evidence)
            if set(result.evidence) != set(evidence_types):
                return "结果 Evidence 类型与收据不一致"
        if result.provenance:
            source_ids = tuple(item.source_id for item in receipt.provenance)
            if set(result.provenance) != set(source_ids):
                return "结果 Provenance 来源与收据不一致"
        return None

    def _validate_artifacts(
        self,
        *,
        receipt: ExecutionReceipt,
        manifest: CapabilityManifest,
        result: CapabilityResult,
    ) -> str | None:
        artifacts = receipt.artifacts
        artifact_ids = tuple(item.artifact_id for item in artifacts)
        if len(set(artifact_ids)) != len(artifact_ids):
            return "收据 artifact 集合含重复 ID"
        contract = manifest.completion_contract
        evidence_contract = manifest.evidence_contract
        minimum = max(contract.minimum_artifact_count, evidence_contract.minimum_artifact_count)
        required = set(contract.required_artifacts) | set(evidence_contract.required_artifacts)
        if len(artifacts) < minimum or not required.issubset(set(artifact_ids)):
            return "收据 artifact 数量或要求不满足契约"
        if minimum or required:
            if not artifacts:
                return "当前能力要求 Artifact，但收据未显式提供"
        for artifact in artifacts:
            if not isinstance(artifact, ArtifactReceipt):
                return "收据 Artifact 摘要类型无效"
            if not artifact.registered:
                return "收据 Artifact 尚未登记"
            if not artifact.content_hash:
                return "收据 Artifact 缺少内容哈希"
            if not artifact.summary:
                return "收据 Artifact 缺少非空摘要"
            status = artifact.summary.get("status")
            if isinstance(status, str) and status in _REJECTED_ARTIFACT_STATUSES:
                return "收据 Artifact 处于拒绝或删除状态"
            claimed_hash = artifact.summary.get("content_hash", artifact.summary.get("sha256"))
            if claimed_hash is not None and str(claimed_hash) != artifact.content_hash:
                return "Artifact 摘要哈希与收据哈希不一致"
            result_hash = result.hashes.get(artifact.artifact_id) or result.hashes.get(
                f"artifact:{artifact.artifact_id}"
            )
            if result_hash is not None and not _hash_matches(
                str(result_hash), artifact.content_hash
            ):
                return "结果 Artifact 哈希与收据不一致"
        return None

    def _validate_drafts(
        self,
        *,
        receipt: ExecutionReceipt,
        manifest: CapabilityManifest,
    ) -> tuple[str | None, tuple[VerifiedEvidenceSpec, ...], tuple[VerifiedProvenanceSpec, ...]]:
        contract = manifest.evidence_contract
        if len(receipt.evidence) < contract.minimum_evidence_count:
            return "收据 Evidence 数量低于契约", (), ()
        required_types = set(contract.required_evidence_types)
        available_types = {item.evidence_type for item in receipt.evidence}
        if not required_types.issubset(available_types):
            return "收据缺少 Evidence contract 要求的证据类型", (), ()
        artifact_ids = {item.artifact_id for item in receipt.artifacts}
        verified_evidence: list[VerifiedEvidenceSpec] = []
        for draft in receipt.evidence:
            if not draft.evidence_type or not draft.statement or not draft.content_hash:
                return "收据 Evidence 缺少类型、陈述或哈希", (), ()
            if contract.reviewer_required and not draft.reviewer_id:
                return "Evidence contract 要求受信 reviewer", (), ()
            if len(set(draft.artifact_ids)) != len(draft.artifact_ids):
                return "收据 Evidence artifact_ids 不得重复", (), ()
            if any(item not in artifact_ids for item in draft.artifact_ids):
                return "收据 Evidence 引用了未在收据中登记的 Artifact", (), ()
            raw_hashes = (
                draft.metadata.get("artifact_hashes")
                if isinstance(draft.metadata, Mapping)
                else None
            )
            if raw_hashes is not None:
                if not isinstance(raw_hashes, Mapping):
                    return "Evidence artifact_hashes 格式无效", (), ()
                for artifact_id in draft.artifact_ids:
                    artifact = next(
                        item for item in receipt.artifacts if item.artifact_id == artifact_id
                    )
                    if str(raw_hashes.get(artifact_id, "")) != artifact.content_hash:
                        return "Evidence artifact 哈希与 Artifact 摘要不一致", (), ()
            verified_evidence.append(
                VerifiedEvidenceSpec(
                    evidence_type=draft.evidence_type,
                    statement=draft.statement,
                    artifact_ids=draft.artifact_ids,
                    source_uri=draft.source_uri,
                    content_hash=draft.content_hash,
                    reviewer_id=draft.reviewer_id,
                    metadata={**dict(draft.metadata), "receipt_id": receipt.receipt_id},
                )
            )
        claimed_scope = ()
        for evidence in receipt.evidence:
            scope = (
                evidence.metadata.get("claims_scope")
                if isinstance(evidence.metadata, Mapping)
                else None
            )
            if scope is not None:
                if not isinstance(scope, (list, tuple)):
                    return "Evidence claims_scope 格式无效", (), ()
                claimed_scope = tuple(str(item) for item in scope)
                if any(item not in set(contract.claims_scope) for item in claimed_scope):
                    return "科学主张超出 Evidence contract 范围", (), ()
            if isinstance(evidence.metadata, Mapping) and any(
                bool(evidence.metadata.get(item)) for item in _FORBIDDEN_SCIENTIFIC_CLAIMS
            ):
                return "计算 Evidence 不得冒充实验或临床结论", (), ()
        del claimed_scope

        verified_provenance: list[VerifiedProvenanceSpec] = []
        for draft in receipt.provenance:
            if not draft.source_type or not draft.source_id or not draft.relation_type:
                return "收据 Provenance 缺少来源或关系", (), ()
            verified_provenance.append(
                VerifiedProvenanceSpec(
                    source_type=draft.source_type,
                    source_id=draft.source_id,
                    relation_type=draft.relation_type,
                    metadata={**dict(draft.metadata), "receipt_id": receipt.receipt_id},
                )
            )
        if contract.require_provenance and not verified_provenance:
            return "当前能力要求 Provenance，但收据未提供", (), ()
        return None, tuple(verified_evidence), tuple(verified_provenance)

    def verify(
        self,
        *,
        manifest: CapabilityManifest,
        result: CapabilityResult,
        invocation: InvocationRecord,
        task: ScientificTaskRecord,
        attempt: ExecutionAttemptRecord,
    ) -> EvidenceGateDecision:
        receipt = result.receipt
        if not isinstance(receipt, ExecutionReceipt):
            return self._deny("结果缺少受信 ExecutionReceipt")
        authentic, reason = self._authentic(
            receipt=receipt,
            manifest=manifest,
            invocation=invocation,
            task=task,
            attempt=attempt,
            result=result,
        )
        if not authentic:
            return self._deny(reason or "ReceiptVerifier 拒绝收据")
        try:
            reason = self._validate_identity(
                receipt=receipt,
                manifest=manifest,
                invocation=invocation,
                task=task,
                attempt=attempt,
                result=result,
            )
            if reason:
                return self._deny(reason)
            reason = self._validate_artifacts(receipt=receipt, manifest=manifest, result=result)
            if reason:
                return self._deny(reason)
            reason, evidence, provenance = self._validate_drafts(receipt=receipt, manifest=manifest)
            if reason:
                return self._deny(reason)
            contract = manifest.completion_contract
            if contract.requires_terminal_status and not result.terminal:
                return self._deny("结果尚未进入终态")
            if result.status.value not in contract.accepted_statuses:
                return self._deny("结果状态不满足 CompletionContract")
            if contract.requires_structured_output and not isinstance(result.output, Mapping):
                return self._deny("结果缺少结构化输出")
            if any(
                not _output_has(result.output, field) for field in contract.required_output_fields
            ):
                return self._deny("结果缺少 CompletionContract 要求的输出字段")
            return EvidenceGateDecision(
                True,
                "受信 Receipt、Artifact、Evidence、Provenance 和完成契约均已核验",
                self.policy_id,
                evidence=evidence,
                provenance=provenance,
            )
        except Exception as exc:
            # wzf：任何未预期的解析/关系异常都不能把成功事实写入数据库。
            return self._deny(f"ReceiptEvidenceGate 核验异常: {type(exc).__name__}")


# 更明确的生产命名，保留短名称供执行器注入。
ProductionReceiptEvidenceGate = ReceiptEvidenceGate
ReceiptEvidenceVerifier = ReceiptVerifier


__all__ = [
    "DEFAULT_RECEIPT_ISSUER",
    "ProductionReceiptEvidenceGate",
    "RECEIPT_EVIDENCE_POLICY_ID",
    "ReceiptEvidenceGate",
    "ReceiptEvidenceVerifier",
    "ReceiptVerification",
    "ReceiptVerifier",
    "canonical_hash",
    "canonical_json_hash",
]
