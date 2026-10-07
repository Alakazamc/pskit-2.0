"""Centralized error codes and error handling utilities."""

from enum import Enum
from typing import Dict


class ErrorCode(str, Enum):
    """Centralized error codes for all services."""

    # MCP Protocol errors
    MCP_REPORT_REQUIRED = "MCP_REPORT_REQUIRED"
    MCP_REPORT_TOO_LARGE = "MCP_REPORT_TOO_LARGE"
    MCP_REMOTE_OUTPUT_INVALID = "MCP_REMOTE_OUTPUT_INVALID"
    MCP_FAILED_USAGE_MISSING = "MCP_FAILED_USAGE_MISSING"
    MCP_RESULT_POINTER_INVALID = "MCP_RESULT_POINTER_INVALID"
    MCP_RESULT_POINTER_MISSING = "MCP_RESULT_POINTER_MISSING"
    MCP_RESULT_TRANSFORM_UNSUPPORTED = "MCP_RESULT_TRANSFORM_UNSUPPORTED"
    MCP_REPORT_ERROR_MISMATCH = "MCP_REPORT_ERROR_MISMATCH"
    MCP_STATUS_TOOL_REQUIRED = "MCP_STATUS_TOOL_REQUIRED"
    MCP_TASKS_NOT_NEGOTIATED = "MCP_TASKS_NOT_NEGOTIATED"
    MCP_JOB_ID_OVERRIDE = "MCP_JOB_ID_OVERRIDE"
    MCP_IMMEDIATE_PENDING = "MCP_IMMEDIATE_PENDING"
    EXTERNAL_JOB_CONFLICT = "EXTERNAL_JOB_CONFLICT"
    ARTIFACT_TOOL_REQUIRED = "ARTIFACT_TOOL_REQUIRED"
    ARTIFACT_READ_FAILED = "ARTIFACT_READ_FAILED"

    # Compute service errors
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    COMPUTE_FAILURE = "COMPUTE_FAILURE"
    SERVICE_MISMATCH = "SERVICE_MISMATCH"
    CAPABILITY_NOT_REGISTERED = "CAPABILITY_NOT_REGISTERED"
    CAPABILITY_SCHEMA_MISMATCH = "CAPABILITY_SCHEMA_MISMATCH"
    ARGUMENTS_MISMATCH = "ARGUMENTS_MISMATCH"
    DUPLICATE_TOOL = "DUPLICATE_TOOL"
    REQUIRED_USAGE_MISSING = "REQUIRED_USAGE_MISSING"

    # CORAL specific errors
    CORAL_PDB_DOWNLOAD_FAILED = "CORAL_PDB_DOWNLOAD_FAILED"
    CORAL_STRUCTURE_INVALID = "CORAL_STRUCTURE_INVALID"
    CORAL_TIMEOUT = "CORAL_TIMEOUT"

    # Pi RPC errors
    PI_RUN_FAILED = "PI_RUN_FAILED"
    PI_RPC_ERROR = "PI_RPC_ERROR"
    MODEL_GATEWAY_QUOTA_EXHAUSTED = "MODEL_GATEWAY_QUOTA_EXHAUSTED"
    MODEL_GATEWAY_AUTH_FAILED = "MODEL_GATEWAY_AUTH_FAILED"
    MODEL_GATEWAY_RATE_LIMITED = "MODEL_GATEWAY_RATE_LIMITED"
    MODEL_REASONING_UNAVAILABLE = "MODEL_REASONING_UNAVAILABLE"
    TOKEN_QUOTA_EXCEEDED = "TOKEN_QUOTA_EXCEEDED"

    # Auth errors
    AUTH_REQUIRED = "AUTH_REQUIRED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    CAPTCHA_INVALID = "CAPTCHA_INVALID"
    CAPTCHA_UNAVAILABLE = "CAPTCHA_UNAVAILABLE"
    CSRF_REJECTED = "CSRF_REJECTED"
    AUTH_ABUSE_LIMITED = "AUTH_ABUSE_LIMITED"

    # Resource errors
    COMPUTE_LEASE_CONFLICT = "COMPUTE_LEASE_CONFLICT"
    GPU_RECONCILIATION_CONFLICT = "GPU_RECONCILIATION_CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    SANDBOX_CONFLICT = "SANDBOX_CONFLICT"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    GUEST_ACCOUNT_DELETING = "GUEST_ACCOUNT_DELETING"

    # File upload errors
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_TYPE_INVALID = "FILE_TYPE_INVALID"
    FILE_CONTENT_INVALID = "FILE_CONTENT_INVALID"

    # Generic errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"


class ErrorSeverity(str, Enum):
    """Error severity levels for logging and monitoring."""
    EXPECTED = "expected"      # User input errors, expected failures
    TRANSIENT = "transient"    # Network timeouts, temporary unavailability
    CRITICAL = "critical"      # Unexpected errors requiring immediate attention


ERROR_MESSAGES: Dict[ErrorCode, str] = {
    # MCP errors
    ErrorCode.MCP_REPORT_REQUIRED: "远程服务未返回有效的计算报告",
    ErrorCode.MCP_REPORT_TOO_LARGE: "计算结果超出大小限制（最大1MB）",
    ErrorCode.MCP_REMOTE_OUTPUT_INVALID: "远程服务返回的数据格式无效",
    ErrorCode.MCP_FAILED_USAGE_MISSING: "失败的任务缺少用量信息（将标记为待核算）",

    # CORAL errors
    ErrorCode.CORAL_PDB_DOWNLOAD_FAILED: "PDB结构下载失败，请检查PDB ID是否正确",
    ErrorCode.CORAL_STRUCTURE_INVALID: "PDB结构文件格式无效",
    ErrorCode.CORAL_TIMEOUT: "CORAL计算超时",

    # Pi RPC errors
    ErrorCode.PI_RUN_FAILED: "Pi运行失败",
    ErrorCode.MODEL_GATEWAY_QUOTA_EXHAUSTED: "模型网关配额已用尽",
    ErrorCode.MODEL_GATEWAY_AUTH_FAILED: "模型网关认证失败",
    ErrorCode.MODEL_GATEWAY_RATE_LIMITED: "模型网关请求频率受限",
    ErrorCode.TOKEN_QUOTA_EXCEEDED: "Token配额已超出限制",

    # Auth errors
    ErrorCode.AUTH_REQUIRED: "需要身份验证",
    ErrorCode.CAPTCHA_REQUIRED: "需要验证码验证",
    ErrorCode.LOGIN_REQUIRED: "需要登录",

    # Generic errors
    ErrorCode.INTERNAL_ERROR: "内部服务错误",
    ErrorCode.TIMEOUT: "操作超时",
    ErrorCode.UNAVAILABLE: "服务暂时不可用",
}

ERROR_SEVERITY: Dict[ErrorCode, ErrorSeverity] = {
    # Expected errors
    ErrorCode.AUTH_REQUIRED: ErrorSeverity.EXPECTED,
    ErrorCode.CAPTCHA_REQUIRED: ErrorSeverity.EXPECTED,
    ErrorCode.FILE_TOO_LARGE: ErrorSeverity.EXPECTED,
    ErrorCode.FILE_TYPE_INVALID: ErrorSeverity.EXPECTED,
    ErrorCode.CORAL_PDB_DOWNLOAD_FAILED: ErrorSeverity.EXPECTED,

    # Transient errors
    ErrorCode.TIMEOUT: ErrorSeverity.TRANSIENT,
    ErrorCode.UNAVAILABLE: ErrorSeverity.TRANSIENT,
    ErrorCode.MODEL_GATEWAY_RATE_LIMITED: ErrorSeverity.TRANSIENT,
    ErrorCode.CORAL_TIMEOUT: ErrorSeverity.TRANSIENT,

    # Critical errors
    ErrorCode.INTERNAL_ERROR: ErrorSeverity.CRITICAL,
    ErrorCode.CAPABILITY_SCHEMA_MISMATCH: ErrorSeverity.CRITICAL,
    ErrorCode.GPU_RECONCILIATION_CONFLICT: ErrorSeverity.CRITICAL,
}


def get_error_message(code: ErrorCode, default: str = "发生错误") -> str:
    """Get user-friendly error message for error code."""
    return ERROR_MESSAGES.get(code, default)


def get_error_severity(code: ErrorCode) -> ErrorSeverity:
    """Get severity level for error code."""
    return ERROR_SEVERITY.get(code, ErrorSeverity.CRITICAL)
