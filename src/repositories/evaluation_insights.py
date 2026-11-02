from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection

from src.models import TimeWindow
from src.models.evaluation_insights import HistoryWindowQuery
from src.repositories.time_window import apply_started_at_window

RUN_LIMIT = 10_000
CASE_LIMIT = 100_000


class InsightsCapacityError(ValueError):
    """Refuse an oversized analysis rather than silently sampling it."""


def insights_window(query: HistoryWindowQuery, now: datetime | None = None) -> TimeWindow:
    now = now or datetime.now(UTC)
    end = query.window_end or now
    if end > now:
        raise ValueError("window_end cannot be in the future")
    end = end.astimezone(UTC)
    return TimeWindow(started_from=end - timedelta(days=int(query.period[:-1])), started_before=end)


async def load_runs(
    collection: AsyncCollection[dict[str, Any]], eval_type: str, window: TimeWindow
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"eval_type": eval_type}
    apply_started_at_window(query, window)
    projection = dict.fromkeys(
        [
            "run_id",
            "eval_type",
            "skill",
            "stage",
            "target",
            "environment",
            "unit_config",
            "summary",
            "execution_status",
            "verdict",
            "started_at",
            "git_ref",
            "git_sha",
            "github_repository",
            "actor",
        ],
        1,
    )
    result = (
        await collection.find(query, projection)
        .sort([("started_at", 1), ("_id", 1)])
        .limit(RUN_LIMIT + 1)
        .to_list(length=RUN_LIMIT + 1)
    )
    if len(result) > RUN_LIMIT:
        raise InsightsCapacityError("Too many runs for Insights. Choose a shorter time window.")
    return result


async def load_cases(
    collection: AsyncCollection[dict[str, Any]], eval_type: str, run_ids: list[str]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    # Avoid BSON command limits and never fetch prompts, responses or tool traces.
    projection = dict.fromkeys(
        [
            "run_id",
            "case_id",
            "suite",
            "skill",
            "test_name",
            "test_type",
            "verdict",
            "scores",
            "score",
        ],
        1,
    )
    for start in range(0, len(run_ids), 500):
        remaining = CASE_LIMIT + 1 - len(result)
        query = {"eval_type": eval_type, "run_id": {"$in": run_ids[start : start + 500]}}
        batch = (
            await collection.find(query, projection)
            .sort([("_id", 1)])
            .limit(remaining)
            .to_list(length=remaining)
        )
        result.extend(batch)
        if len(result) > CASE_LIMIT:
            raise InsightsCapacityError(
                "Too many case results for Insights. Narrow the scope or time window."
            )
    return result
