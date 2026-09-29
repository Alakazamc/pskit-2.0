"""AI4S Harness 的持久化状态枚举。

状态值使用小写字符串，以便与数据库、JSON 事件和蓝图中的领域词汇保持
一致。枚举没有绑定 SQLAlchemy 类型；调用方可以显式使用 ``.value``。
"""

from enum import Enum


class _StringEnum(str, Enum):
    """让枚举同时适用于 JSON 字符串和普通字符串比较。"""

    def __str__(self) -> str:
        return self.value


class ResearchSessionStatus(_StringEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETING = "deleting"
    DELETED = "deleted"


class ResearchGoalStatus(_StringEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    WAITING_FOR_INPUT = "waiting_for_input"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class InteractionTurnStatus(_StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    WAITING_FOR_INPUT = "waiting_for_input"


class SkillExecutionStatus(_StringEnum):
    PLANNED = "planned"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RUNNING = "running"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    WAITING_FOR_INPUT = "waiting_for_input"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ScientificTaskStatus(_StringEnum):
    PLANNED = "planned"
    PENDING = "pending"
    QUEUED = "queued"
    BLOCKED = "blocked"
    RUNNING = "running"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    WAITING_FOR_INPUT = "waiting_for_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskGraphStatus(_StringEnum):
    """任务图本身的生命周期；兼容 SkillExecution 的等待态。"""

    DRAFT = "draft"
    PLANNED = "planned"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RUNNING = "running"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    WAITING_FOR_INPUT = "waiting_for_input"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionAttemptStatus(_StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class OutboxEventStatus(_StringEnum):
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class EvidenceStatus(_StringEnum):
    UNREVIEWED = "unreviewed"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class CandidateSetStatus(_StringEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNVERIFIED = "unverified"


class ScientificCandidateStatus(_StringEnum):
    GENERATED = "generated"
    ACTIVE = "active"
    SCORED = "scored"
    SELECTED = "selected"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    UNVERIFIED = "unverified"


class ScoreRunStatus(_StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNVERIFIED = "unverified"


class CandidateScoreSelectionStatus(_StringEnum):
    PENDING = "pending"
    QUALIFIED = "qualified"
    SELECTED = "selected"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    UNVERIFIED = "unverified"


class StructurePredictionRunStatus(_StringEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    BLOCKED = "blocked"
    MISSING_ARTIFACT = "missing_artifact"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DecisionPolicyStatus(_StringEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    RETIRED = "retired"


class StrategyFeedbackStatus(_StringEnum):
    RECORDED = "recorded"
    APPLIED = "applied"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    UNVERIFIED = "unverified"


class CapabilityInvocationStatus(_StringEnum):
    QUEUED = "queued"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RUNNING = "running"
    WAITING_FOR_RESOURCE = "waiting_for_resource"
    WAITING_FOR_DEPENDENCY = "waiting_for_dependency"
    WAITING_FOR_INPUT = "waiting_for_input"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    BLOCKED = "blocked"
    REJECTED = "rejected"


class CapabilityExecutionMode(_StringEnum):
    SYNC = "sync"
    SYNCHRONOUS = "sync"
    ASYNC = "async"
    ASYNCHRONOUS = "async"
    STREAM = "stream"
    STREAMING = "stream"
    BATCH = "batch"


class CapabilityRiskLevel(_StringEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
