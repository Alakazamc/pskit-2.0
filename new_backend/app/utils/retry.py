"""Retry utilities for handling transient failures."""

import asyncio
from functools import wraps
from typing import Callable, Type, TypeVar, Tuple

from app.domain.errors import ErrorCode, ErrorSeverity, get_error_severity
from app.services.structured_logging import get_logger

T = TypeVar('T')

logger = get_logger("pskit.retry")

# Transient errors that should be retried
TRANSIENT_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    OSError,
    TimeoutError,
    ConnectionError,
    asyncio.TimeoutError,
)


def should_retry_error(error: Exception, error_code: str | None = None) -> bool:
    """Determine if an error is transient and should be retried."""
    # Check if it's a known transient exception type
    if isinstance(error, TRANSIENT_EXCEPTIONS):
        return True

    # Check error code severity if available
    if error_code:
        try:
            code_enum = ErrorCode(error_code)
            severity = get_error_severity(code_enum)
            return severity == ErrorSeverity.TRANSIENT
        except (ValueError, KeyError):
            pass

    return False


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 60.0,
    exponential: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = TRANSIENT_EXCEPTIONS,
):
    """Decorator for retrying async functions with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        base_delay_seconds: Initial delay between retries
        max_delay_seconds: Maximum delay between retries
        exponential: Whether to use exponential backoff (2^attempt * base_delay)
        retryable_exceptions: Tuple of exception types that should trigger a retry

    Usage:
        @retry_with_backoff(max_attempts=3, base_delay_seconds=1.0)
        async def fetch_data():
            # Your async code here
            pass
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_error = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_error = e

                    if attempt < max_attempts - 1:
                        # Calculate delay with exponential backoff
                        if exponential:
                            delay = min(
                                base_delay_seconds * (2 ** attempt),
                                max_delay_seconds
                            )
                        else:
                            delay = base_delay_seconds

                        logger.warning(
                            f"Attempt {attempt + 1}/{max_attempts} failed, retrying in {delay}s",
                            function=func.__name__,
                            error_type=type(e).__name__,
                            error_message=str(e),
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            delay_seconds=delay
                        )

                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"All {max_attempts} attempts failed",
                            function=func.__name__,
                            error_type=type(e).__name__,
                            error_message=str(e)
                        )

            # Re-raise the last error if all attempts failed
            raise last_error

        return wrapper
    return decorator


async def retry_with_timeout(
    coro,
    timeout_seconds: float,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
) -> T:
    """Execute a coroutine with timeout and retry logic.

    Args:
        coro: Coroutine to execute
        timeout_seconds: Timeout for each attempt
        max_attempts: Maximum retry attempts
        base_delay_seconds: Base delay between retries

    Returns:
        Result of the coroutine

    Raises:
        asyncio.TimeoutError: If all attempts timeout
    """
    last_error = None

    for attempt in range(max_attempts):
        try:
            async with asyncio.timeout(timeout_seconds):
                return await coro
        except asyncio.TimeoutError as e:
            last_error = e

            if attempt < max_attempts - 1:
                delay = base_delay_seconds * (2 ** attempt)
                logger.warning(
                    f"Attempt {attempt + 1}/{max_attempts} timed out, retrying",
                    timeout_seconds=timeout_seconds,
                    attempt=attempt + 1,
                    delay_seconds=delay
                )
                await asyncio.sleep(delay)

    logger.error(
        f"All {max_attempts} attempts timed out",
        timeout_seconds=timeout_seconds
    )
    raise last_error


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay_seconds: float = 1.0,
        max_delay_seconds: float = 60.0,
        timeout_seconds: float | None = None,
    ):
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self.timeout_seconds = timeout_seconds

    def decorator(self):
        """Get a retry decorator with this configuration."""
        return retry_with_backoff(
            max_attempts=self.max_attempts,
            base_delay_seconds=self.base_delay_seconds,
            max_delay_seconds=self.max_delay_seconds,
        )
