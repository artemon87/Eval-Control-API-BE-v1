from datetime import UTC, datetime

import pytest

from src.models import TimeWindow
from src.repositories.time_window import apply_started_at_window


def test_applies_half_open_utc_window() -> None:
    query: dict[str, object] = {}
    apply_started_at_window(
        query,
        TimeWindow(
            started_from=datetime(2026, 10, 1),
            started_before=datetime(2026, 10, 8, tzinfo=UTC),
        ),
    )
    assert query == {
        "started_at": {
            "$gte": datetime(2026, 10, 1, tzinfo=UTC),
            "$lt": datetime(2026, 10, 8, tzinfo=UTC),
        }
    }


def test_rejects_empty_or_reversed_window() -> None:
    with pytest.raises(ValueError, match="started_from must be earlier"):
        apply_started_at_window(
            {},
            TimeWindow(
                started_from=datetime(2026, 10, 8, tzinfo=UTC),
                started_before=datetime(2026, 10, 1, tzinfo=UTC),
            ),
        )
