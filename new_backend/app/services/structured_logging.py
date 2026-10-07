"""Structured logging utilities with request context tracking."""

import json
import logging
from contextvars import ContextVar
from typing import Any

from app.domain.errors import ErrorCode, get_error_severity

# Context variable to track request-scoped data
request_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "request_context", default=None
)


class StructuredLogger:
    """Logger that emits structured JSON with request context."""

    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        self.name = name

    def _log(
        self,
        level: int,
        message: str,
        error_code: str | None = None,
        **extra_fields
    ):
        """Log with structured data including request context."""
        context = request_context.get() or {}

        log_data = {
            "message": message,
            "logger": self.name,
            **context,
            **extra_fields
        }

        if error_code:
            log_data["error_code"] = error_code
            # Add severity if it's a known error code
            try:
                code_enum = ErrorCode(error_code)
                severity = get_error_severity(code_enum)
                log_data["severity"] = severity.value
            except (ValueError, KeyError):
                pass

        # Format as JSON for structured logging systems
        formatted = json.dumps(log_data, ensure_ascii=False, default=str)
        self.logger.log(level, formatted)

    def debug(self, message: str, **kwargs):
        """Log debug message with context."""
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs):
        """Log info message with context."""
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs):
        """Log warning message with context."""
        self._log(logging.WARNING, message, **kwargs)

    def error(
        self,
        message: str,
        error_code: str | None = None,
        exc_info: bool = False,
        **kwargs
    ):
        """Log error message with context and optional exception info."""
        if exc_info:
            kwargs["exc_info"] = True
        self._log(logging.ERROR, message, error_code=error_code, **kwargs)

    def critical(self, message: str, error_code: str | None = None, **kwargs):
        """Log critical message with context."""
        self._log(logging.CRITICAL, message, error_code=error_code, **kwargs)


def set_request_context(
    user_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    request_id: str | None = None,
    **extra
):
    """Set request context for structured logging."""
    context = {}
    if user_id:
        context["user_id"] = user_id
    if run_id:
        context["run_id"] = run_id
    if task_id:
        context["task_id"] = task_id
    if request_id:
        context["request_id"] = request_id
    context.update(extra)
    request_context.set(context)


def clear_request_context():
    """Clear request context."""
    request_context.set({})


# Convenience function for common loggers
def get_logger(name: str) -> StructuredLogger:
    """Get or create a structured logger."""
    return StructuredLogger(name)


# Pre-configured loggers for common subsystems
compute_logger = StructuredLogger("pskit.compute")
mcp_logger = StructuredLogger("pskit.mcp")
coral_logger = StructuredLogger("pskit.coral")
pi_logger = StructuredLogger("pskit.pi")
auth_logger = StructuredLogger("pskit.auth")
