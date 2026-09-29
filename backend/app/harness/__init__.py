"""PSKit AI4S Harness 的领域底座。

本包只提供稳定的状态、事件和旧数据迁移辅助类型。本包导入本身不注册
数据库模型、不改变旧 Agent 运行时，也不通过导入配置文件连接数据库。
"""

from .enums import (
    CapabilityExecutionMode,
    CapabilityInvocationStatus,
    CapabilityRiskLevel,
    CandidateScoreSelectionStatus,
    CandidateSetStatus,
    DecisionPolicyStatus,
    EvidenceStatus,
    ExecutionAttemptStatus,
    InteractionTurnStatus,
    OutboxEventStatus,
    ResearchGoalStatus,
    ResearchSessionStatus,
    ScoreRunStatus,
    ScientificCandidateStatus,
    ScientificTaskStatus,
    SkillExecutionStatus,
    StrategyFeedbackStatus,
    StructurePredictionRunStatus,
    TaskGraphStatus,
)
from .events import DomainEvent, build_dedupe_key
from .migration import (
    LegacyMigrationIssue,
    LegacyMigrationPlan,
    LegacyMigrationResult,
    MigrationIssue,
    MigrationOperation,
    MigrationResult,
    apply_legacy_migration,
    inspect_legacy,
    plan_legacy_migration,
)

__all__ = [
    "CapabilityExecutionMode",
    "CapabilityInvocationStatus",
    "CapabilityRiskLevel",
    "CandidateScoreSelectionStatus",
    "CandidateSetStatus",
    "DecisionPolicyStatus",
    "DomainEvent",
    "EvidenceStatus",
    "ExecutionAttemptStatus",
    "InteractionTurnStatus",
    "LegacyMigrationIssue",
    "LegacyMigrationPlan",
    "LegacyMigrationResult",
    "MigrationIssue",
    "MigrationOperation",
    "MigrationResult",
    "OutboxEventStatus",
    "ResearchGoalStatus",
    "ResearchSessionStatus",
    "ScoreRunStatus",
    "SQLAlchemyOutboxStore",
    "ScientificCandidateStatus",
    "ScientificTaskStatus",
    "SkillExecutionStatus",
    "StrategyFeedbackStatus",
    "StructurePredictionRunStatus",
    "TaskGraphStatus",
    "DEFAULT_RECEIPT_CLOCK_SKEW",
    "DEFAULT_RECEIPT_MAX_AGE",
    "ED25519_ALGORITHM",
    "Ed25519ReceiptVerifier",
    "RECEIPT_PROOF_VERSION",
    "RECEIPT_SIGNATURE_DOMAIN",
    "RECEIPT_SIGNATURE_DOMAIN_SEPARATOR",
    "ReceiptPublicKeyResolver",
    "ReceiptVerification",
    "ReceiptVerificationKey",
    "ReceiptVerifier",
    "apply_legacy_migration",
    "build_dedupe_key",
    "encode_receipt_proof",
    "inspect_legacy",
    "plan_legacy_migration",
    "receipt_signature_payload",
]


def __getattr__(name: str):
    """按需导入 SQLAlchemy store，避免 ``outbox`` 子模块循环导入。"""

    if name == "SQLAlchemyOutboxStore":
        from .sqlalchemy_outbox import SQLAlchemyOutboxStore

        return SQLAlchemyOutboxStore
    if name in {
        "DEFAULT_RECEIPT_CLOCK_SKEW",
        "DEFAULT_RECEIPT_MAX_AGE",
        "ED25519_ALGORITHM",
        "Ed25519ReceiptVerifier",
        "RECEIPT_PROOF_VERSION",
        "RECEIPT_SIGNATURE_DOMAIN",
        "RECEIPT_SIGNATURE_DOMAIN_SEPARATOR",
        "ReceiptPublicKeyResolver",
        "ReceiptVerification",
        "ReceiptVerificationKey",
        "ReceiptVerifier",
        "encode_receipt_proof",
        "receipt_signature_payload",
    }:
        from . import receipt_verifier

        return getattr(receipt_verifier, name)
    raise AttributeError(name)
