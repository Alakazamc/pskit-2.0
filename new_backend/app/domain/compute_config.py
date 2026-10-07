"""Enhanced configuration management with retry and timeout settings."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrySettings:
    """Retry configuration for a specific operation type."""
    max_attempts: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    timeout_seconds: float = 30.0
    exponential_backoff: bool = True


@dataclass(frozen=True)
class ComputeRetryConfig:
    """Retry settings for various compute operations."""

    # MCP service calls
    mcp_call: RetrySettings = field(default_factory=lambda: RetrySettings(
        max_attempts=3,
        base_delay_seconds=2.0,
        max_delay_seconds=60.0,
        timeout_seconds=30.0
    ))

    # CORAL specific operations
    coral_submit: RetrySettings = field(default_factory=lambda: RetrySettings(
        max_attempts=5,
        base_delay_seconds=1.0,
        max_delay_seconds=30.0,
        timeout_seconds=600.0  # 10 minutes for structure download
    ))

    # AF3 operations
    af3_submit: RetrySettings = field(default_factory=lambda: RetrySettings(
        max_attempts=5,
        base_delay_seconds=2.0,
        max_delay_seconds=120.0,
        timeout_seconds=21600.0  # 6 hours
    ))

    # Artifact transfer
    artifact_transfer: RetrySettings = field(default_factory=lambda: RetrySettings(
        max_attempts=3,
        base_delay_seconds=1.0,
        max_delay_seconds=30.0,
        timeout_seconds=120.0
    ))


@dataclass(frozen=True)
class PollingConfig:
    """Configuration for polling operations."""

    # Status check intervals
    mcp_poll_interval_seconds: float = 0.25
    af3_poll_interval_seconds: float = 1.0
    coral_poll_interval_seconds: float = 0.5

    # Lease renewal timing
    lease_renewal_ratio: float = 0.33  # Renew at 1/3 of lease duration

    # Background task intervals
    cleanup_interval_seconds: float = 60.0
    health_check_interval_seconds: float = 30.0


@dataclass(frozen=True)
class ResourceLimits:
    """Resource limits and thresholds."""

    # Report size limits
    max_report_bytes: int = 1024 * 1024  # 1MB
    max_artifact_count: int = 128
    max_artifact_bytes: int = 100 * 1024 * 1024  # 100MB per artifact

    # PDF processing
    max_pdf_pages: int = 100
    max_pdf_bytes: int = 10 * 1024 * 1024  # 10MB

    # Image processing
    max_image_bytes: int = 4 * 1024 * 1024  # 4MB
    max_images_per_message: int = 10

    # Text files
    max_text_file_bytes: int = 1024 * 1024  # 1MB
    max_extracted_chars: int = 200_000

    # Concurrent operations
    max_concurrent_mcp_calls: int = 8
    max_concurrent_pdf_parses: int = 2
    max_active_runs_per_user: int = 2
    max_active_runs_total: int = 4


# Global default configurations
DEFAULT_RETRY_CONFIG = ComputeRetryConfig()
DEFAULT_POLLING_CONFIG = PollingConfig()
DEFAULT_RESOURCE_LIMITS = ResourceLimits()
