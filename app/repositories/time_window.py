from datetime import UTC, datetime
from typing import Any


def normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def apply_started_at_window(
    query: dict[str, Any],
    *,
    started_from: datetime | None,
    started_before: datetime | None,
) -> None:
    """Apply one stable, half-open UTC window to a Mongo query."""
    window_start = normalize_utc(started_from)
    window_end = normalize_utc(started_before)
    if window_start and window_end and window_start >= window_end:
        raise ValueError("started_from must be earlier than started_before")

    bounds: dict[str, datetime] = {}
    if window_start:
        bounds["$gte"] = window_start
    if window_end:
        bounds["$lt"] = window_end
    if bounds:
        query["started_at"] = bounds

