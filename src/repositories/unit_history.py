import base64
import binascii
import json
from datetime import UTC, timedelta
from typing import Any

from pymongo.asynchronous.collection import AsyncCollection

from src.models import TimeWindow
from src.models.unit_history import UnitHistoryScope
from src.repositories.base import normalize_document
from src.repositories.time_window import apply_started_at_window

HISTORY_SCOPE_FIELDS = ("skill", "github_repository", "stage", "target_environment", "environment")


def _trimmed(field: str, fallback: Any = None) -> dict[str, Any]:
    return {
        "$let": {
            "vars": {
                "value": {
                    "$cond": [
                        {"$eq": [{"$type": f"${field}"}, "string"]},
                        {"$trim": {"input": f"${field}"}},
                        "",
                    ]
                }
            },
            "in": {"$cond": [{"$ne": ["$$value", ""]}, "$$value", fallback]},
        }
    }


def scope_projection() -> dict[str, Any]:
    environment = _trimmed("environment", "unknown")
    return {
        "skill": "$skill",
        "github_repository": _trimmed("github_repository"),
        "stage": _trimmed("unit_config.stage", environment),
        "target_environment": _trimmed("unit_config.target_environment"),
        "environment": environment,
    }


def make_scope(values: dict[str, Any]) -> UnitHistoryScope:
    payload = [values.get(field) for field in HISTORY_SCOPE_FIELDS]
    token = (
        base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
        )
        .decode()
        .rstrip("=")
    )
    return UnitHistoryScope(scope_key=token, **values)


def decode_scope(token: str) -> UnitHistoryScope:
    try:
        values = json.loads(
            base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(values, list) or len(values) != len(HISTORY_SCOPE_FIELDS):
            raise ValueError("Invalid scope")
        for index, value in enumerate(values):
            if index in {1, 3} and value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Invalid scope")
        scope = make_scope(dict(zip(HISTORY_SCOPE_FIELDS, values, strict=True)))
        if scope.scope_key != token:
            raise ValueError("Noncanonical scope")
        return scope
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("Invalid history scope") from exc


def history_window(window: TimeWindow) -> TimeWindow:
    if window.started_from is None or window.started_before is None:
        raise ValueError("History requires started_from and started_before")
    start = window.started_from
    end = window.started_before
    start = start.replace(tzinfo=UTC) if start.tzinfo is None else start.astimezone(UTC)
    end = end.replace(tzinfo=UTC) if end.tzinfo is None else end.astimezone(UTC)
    if start >= end or end - start > timedelta(days=90):
        raise ValueError("History window must be positive and no longer than 90 days")
    return TimeWindow(started_from=start, started_before=end)


async def list_history_scopes(
    collection: AsyncCollection[dict[str, Any]],
) -> list[UnitHistoryScope]:
    projection = scope_projection()
    cursor = await collection.aggregate(
        [
            {"$match": {"eval_type": "unit", "skill": {"$type": "string", "$ne": ""}}},
            {"$project": projection},
            {"$group": {"_id": {field: f"${field}" for field in HISTORY_SCOPE_FIELDS}}},
            {"$replaceRoot": {"newRoot": "$_id"}},
            {
                "$sort": {
                    "skill": 1,
                    "github_repository": 1,
                    "stage": 1,
                    "target_environment": 1,
                    "environment": 1,
                }
            },
        ]
    )
    return [
        make_scope(item)
        for item in await cursor.to_list(length=None)
        if isinstance(item.get("skill"), str) and item["skill"].strip()
    ]


def history_run_query(scope: UnitHistoryScope, window: TimeWindow) -> dict[str, Any]:
    query: dict[str, Any] = {
        "eval_type": "unit",
        "skill": scope.skill,
        "$expr": {
            "$and": [
                {"$eq": [expression, {"$literal": getattr(scope, field)}]}
                for field, expression in scope_projection().items()
                if field != "skill"
            ]
        },
    }
    apply_started_at_window(query, history_window(window))
    return query


async def load_history_inputs(
    runs_collection: AsyncCollection[dict[str, Any]],
    cases_collection: AsyncCollection[dict[str, Any]],
    scope: UnitHistoryScope,
    window: TimeWindow,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    projection = dict.fromkeys(
        [
            "run_id",
            "started_at",
            "execution_status",
            "verdict",
            "summary",
            "unit_config",
            "git_ref",
            "git_sha",
            "actor",
            "trigger",
            "duration_ms",
            "environment",
        ],
        1,
    )
    cursor = runs_collection.find(history_run_query(scope, window), projection)
    documents = await cursor.sort([("started_at", 1), ("_id", 1)]).to_list(length=None)
    runs = [normalize_document(document) for document in documents]
    run_ids = [run["run_id"] for run in runs if run.get("execution_status") == "completed"]
    observations: list[dict[str, Any]] = []
    # Aggregate only identity, verdict, and comparison metadata, never prompts or tool traces.
    # Chunk IDs to keep queries below BSON limits without truncating the selected window.
    for start in range(0, len(run_ids), 500):
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "eval_type": "unit",
                    "skill": scope.skill,
                    "run_id": {"$in": run_ids[start : start + 500]},
                }
            },
            {
                "$project": {
                    "run_id": 1,
                    "name": _trimmed("test_name", _trimmed("case_id")),
                    "test_type": _trimmed("test_type"),
                    "verdict": {
                        "$cond": [
                            {"$eq": [{"$type": "$verdict"}, "string"]},
                            {"$toLower": {"$trim": {"input": "$verdict"}}},
                            "pending",
                        ]
                    },
                    "definition": {
                        "thresholds": {
                            "$cond": [
                                {"$eq": [{"$type": "$thresholds"}, "object"]},
                                "$thresholds",
                                {},
                            ]
                        },
                        "metrics": {
                            "$map": {
                                "input": {
                                    "$objectToArray": {
                                        "$cond": [
                                            {"$eq": [{"$type": "$scores"}, "object"]},
                                            "$scores",
                                            {},
                                        ]
                                    }
                                },
                                "as": "score",
                                "in": "$$score.k",
                            }
                        },
                    },
                }
            },
            {
                "$group": {
                    "_id": {"run_id": "$run_id", "name": "$name", "test_type": "$test_type"},
                    "verdicts": {"$addToSet": "$verdict"},
                    "definitions": {"$addToSet": "$definition"},
                    "records": {"$sum": 1},
                }
            },
        ]
        aggregated = await cases_collection.aggregate(pipeline)
        observations.extend(await aggregated.to_list(length=None))
    return runs, observations
