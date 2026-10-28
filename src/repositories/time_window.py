from datetime import UTC, datetime
from typing import Any

from src.models import TimeWindow


def _normalize_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def apply_started_at_window(
    query: dict[str, Any],
    time_window: TimeWindow | None,
) -> None:
    if time_window is None:
        return

    window_start = _normalize_utc(time_window.started_from)
    window_end = _normalize_utc(time_window.started_before)
    if window_start and window_end and window_start >= window_end:
        raise ValueError("started_from must be earlier than started_before")

    bounds: dict[str, datetime] = {}
    if window_start:
        bounds["$gte"] = window_start
    if window_end:
        bounds["$lt"] = window_end
    if bounds:
        query["started_at"] = bounds
