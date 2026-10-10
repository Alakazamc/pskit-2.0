"""Protocol validation and explicit accounting windows, never remote measurement."""

from datetime import UTC, datetime, timedelta

from jsonschema import Draft202012Validator

from app.contracts.compute import Completed, Pending


def validate_usage(usage, capability, *, terminal):
    if terminal and any(getattr(usage, metric) is None for metric in capability.required_usage):
        raise ValueError("REQUIRED_USAGE_MISSING")
    if usage.source not in capability.accepted_sources:
        raise ValueError("USAGE_SOURCE_NOT_ALLOWED")


def validate_report(report, capability):
    if isinstance(report, Pending):
        return
    validate_usage(report.usage, capability, terminal=True)
    if isinstance(report, Completed) and not Draft202012Validator(capability.output_schema).is_valid(
        report.result
    ):
        raise ValueError("INVALID_RESULT")


def daily_slices(value, window, fallback_day):
    """Allocate total by explicit UTC window; remainder goes to the last day.

    This proportional accounting policy does not claim physical daily measurement.
    """
    if window is None:
        return {fallback_day: value}
    start, end = window.start.astimezone(UTC), window.end.astimezone(UTC)
    if start == end:
        return {start.date().isoformat(): value}
    total_us = (end-start) // timedelta(microseconds=1)
    cursor, remaining, slices = start, value, {}
    while cursor < end:
        boundary = min(datetime.combine(cursor.date() + timedelta(days=1),
                                        datetime.min.time(), UTC), end)
        if value is None:
            part = None
        elif boundary == end:
            part = remaining
        else:
            part = value * ((boundary-cursor) // timedelta(microseconds=1)) // total_us
            remaining -= part
        slices[cursor.date().isoformat()] = part
        cursor = boundary
    return slices
