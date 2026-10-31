"""Bounded Overview and complete filter discovery, adapted from company screenshots."""

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from itertools import groupby
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from src.config import Settings
from src.models.common import GitFilters, GroupingFilters, TimeWindow
from src.repositories.base import apply_git_filters, normalize_document
from src.repositories.time_window import apply_started_at_window
from src.repositories.unit import UnitRepository, _apply_grouping_filters

INBOX_LIMIT = 20
PROJECTION = {
    key: 1
    for key in (
        "_id",
        "run_id",
        "eval_type",
        "started_at",
        "execution_status",
        "verdict",
        "duration_ms",
        "github_repository",
        "skill",
        "environment",
        "stage",
        "target",
        "summary",
        "unit_config.stage",
        "unit_config.target_environment",
        "unit_config.evaluation_layer",
    )
}
ALIASES = {
    "pass": "passed",
    "fail": "failed",
    "block": "blocked",
    "error": "blocked",
    "xpass": "xpassed",
    "unexpected_pass": "xpassed",
    "unexpected-pass": "xpassed",
}
FIELDS = (
    "runs",
    "completed",
    "passed",
    "failed",
    "blocked",
    "xpassed",
    "degraded",
    "attention",
    "running",
    "queued",
    "total_cases",
    "passed_cases",
)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def resolve_window(window: TimeWindow | None, now: datetime | None = None) -> TimeWindow:
    end = (
        utc(window.started_before) if window and window.started_before else now or datetime.now(UTC)
    )
    start = utc(window.started_from) if window and window.started_from else end - timedelta(days=7)
    if start >= end or end - start > timedelta(days=92):
        raise ValueError("Time window must be positive and cannot exceed 92 days")
    return TimeWindow(started_from=start, started_before=end)


def number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def mean_score(run: dict[str, Any]) -> float:
    summary = run.get("summary") or {}
    direct = number(summary.get("mean_score"))
    if direct is not None:
        return direct
    values = [
        v for raw in (summary.get("mean_scores") or {}).values() if (v := number(raw)) is not None
    ]
    return sum(values) / len(values) if values else 0.0


def scope(run: dict[str, Any]) -> tuple[Any, ...] | None:
    repository = str(run.get("github_repository") or "").strip().lower()
    if not repository:
        return None
    if run["eval_type"] == "e2e":
        return ("e2e", repository, run.get("stage"), run.get("target"))
    config = run.get("unit_config") or {}
    stage = (
        str(config.get("stage") or "").strip()
        or str(run.get("environment") or "").strip()
        or "unknown"
    )
    # Targets and execution environments must not become each other's degradation baseline.
    return (
        "unit",
        repository,
        run.get("skill"),
        stage,
        run.get("environment"),
        config.get("target_environment"),
        config.get("evaluation_layer"),
    )


def comparable(run: dict[str, Any]) -> bool:
    return (
        run.get("execution_status") == "completed"
        and (number((run.get("summary") or {}).get("total")) or 0) > 0
        and scope(run) is not None
    )


def verdict(run: dict[str, Any]) -> str:
    if run.get("execution_status") in {"error", "blocked"}:
        return "blocked"
    raw = str(run.get("verdict") or "pending").strip().lower()
    return ALIASES.get(raw, raw)


def percentile(values: list[float]) -> float | None:
    return sorted(values)[math.ceil(0.95 * len(values)) - 1] if values else None


def build_overview(runs: list[dict[str, Any]], earlier: list[dict[str, Any]]) -> dict[str, Any]:
    previous = {}
    groups = defaultdict(list)
    for run in runs + earlier:
        if comparable(run):
            groups[scope(run)].append(run)
    for items in groups.values():
        prior = None
        items.sort(key=lambda r: (utc(r["started_at"]), str(r.get("_id", ""))))
        for _, simultaneous in groupby(items, key=lambda r: utc(r["started_at"])):
            same_time = list(simultaneous)
            for run in same_time:
                if prior is not None:
                    previous[(run["eval_type"], run["run_id"])] = mean_score(prior)
            prior = same_time[-1]
    counts = defaultdict(lambda: dict.fromkeys(FIELDS, 0))
    durations = defaultdict(list)
    blocked = defaultdict(list)
    attention = defaultdict(list)
    for run in sorted(
        runs, key=lambda r: (utc(r["started_at"]), str(r.get("_id", ""))), reverse=True
    ):
        kind = run["eval_type"]
        date = utc(run["started_at"]).date().isoformat()
        prior_score = previous.get((kind, run["run_id"]))
        degraded = prior_score is not None and mean_score(run) < prior_score
        result = verdict(run)
        is_blocked = result == "blocked"
        needs_attention = not is_blocked and (result in {"failed", "xpassed"} or degraded)
        for key in ((None, "all"), (None, kind), (date, "all"), (date, kind)):
            row = counts[key]
            row["runs"] += 1
            for name, condition in (
                ("blocked", is_blocked),
                ("attention", needs_attention),
                ("degraded", degraded),
                ("running", run.get("execution_status") == "running"),
                ("queued", run.get("execution_status") == "queued"),
            ):
                row[name] += int(condition)
            if run.get("execution_status") == "completed":
                row["completed"] += 1
                for name in ("passed", "failed", "xpassed"):
                    row[name] += int(result == name)
                summary = run.get("summary") or {}
                row["total_cases"] += int(number(summary.get("total")) or 0)
                row["passed_cases"] += int(number(summary.get("passed")) or 0)
                duration = number(run.get("duration_ms"))
                if duration is not None:
                    durations[key].append(duration)
        if is_blocked and len(blocked[kind]) < INBOX_LIMIT:
            blocked[kind].append(run)
        elif needs_attention and len(attention[kind]) < INBOX_LIMIT:
            attention[kind].append(
                {"run": run, "degraded": degraded, "previous_mean_score": prior_score}
            )

    def bucket(key: tuple[Any, str]) -> dict[str, Any]:
        return {
            **counts[key],
            "timed_completed": len(durations[key]),
            "p95_duration_ms": percentile(durations[key]),
        }

    return {
        "totals": {kind: bucket((None, kind)) for kind in ("all", "unit", "e2e")},
        "days": [
            {"date": date, "eval_type": None if kind == "all" else kind, **bucket((date, kind))}
            for date, kind in sorted(k for k in counts if k[0])
        ],
        "recent_runs": [
            run
            for kind in ("unit", "e2e")
            for run in sorted(
                (r for r in runs if r["eval_type"] == kind),
                key=lambda r: utc(r["started_at"]),
                reverse=True,
            )[:5]
        ],
        "blocked_runs": [r for items in blocked.values() for r in items],
        "attention_runs": [r for items in attention.values() for r in items],
    }


class AnalyticsRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]], settings: Settings) -> None:
        self.unit = UnitRepository(database, settings)
        self.collections = {
            "unit": database[settings.unit_runs_collection],
            "e2e": database[settings.e2e_runs_collection],
        }

    async def overview(
        self,
        *,
        time_window: TimeWindow | None = None,
        git_filters: GitFilters | None = None,
        grouping_filters: GroupingFilters | None = None,
        skill: str | None = None,
        agent_name: str | None = None,
        environment: str | None = None,
        e2e_stage: str | None = None,
        e2e_target: str | None = None,
    ) -> dict[str, Any]:
        window = resolve_window(time_window)
        runs, earlier = [], []
        for kind, collection in self.collections.items():
            query: dict[str, Any] = {"eval_type": kind}
            if git_filters:
                apply_git_filters(
                    query, git_filters.github_repository, git_filters.git_ref, git_filters.git_sha
                )
            if kind == "unit":
                _apply_grouping_filters(query, grouping_filters)
                query.update(
                    {
                        k: v
                        for k, v in {
                            "skill": skill,
                            "unit_config.agent_name": agent_name,
                            "environment": environment,
                        }.items()
                        if v
                    }
                )
            else:
                query.update(
                    {k: v for k, v in {"stage": e2e_stage, "target": e2e_target}.items() if v}
                )
            bounded = dict(query)
            apply_started_at_window(bounded, window)
            selected = (
                await collection.find(bounded, PROJECTION)
                .sort([("started_at", -1), ("_id", -1)])
                .to_list(length=None)
            )
            wanted = {scope(r) for r in selected if comparable(r)}
            predecessors = []
            if wanted:
                # Group in Mongo rather than loading every historical run into Python.
                group_fields = {
                    "repository": {"$toLower": {"$trim": {"input": "$github_repository"}}}
                }
                paths = (
                    ("stage", "target")
                    if kind == "e2e"
                    else (
                        "skill",
                        "environment",
                        "unit_config.stage",
                        "unit_config.target_environment",
                        "unit_config.evaluation_layer",
                    )
                )
                group_fields.update(
                    {p.replace(".", "_"): {"$ifNull": ["$" + p, None]} for p in paths}
                )
                match = {
                    **query,
                    "started_at": {"$lt": window.started_from},
                    "execution_status": "completed",
                    "summary.total": {"$gt": 0},
                    "github_repository": {"$type": "string", "$ne": ""},
                }
                match["target" if kind == "e2e" else "skill"] = {
                    "$in": list(
                        {
                            r.get("target" if kind == "e2e" else "skill")
                            for r in selected
                            if comparable(r)
                        }
                    )
                }
                cursor = await collection.aggregate(
                    [
                        {"$match": match},
                        {"$sort": {"started_at": -1, "_id": -1}},
                        {"$project": PROJECTION},
                        {"$group": {"_id": group_fields, "run": {"$first": "$$ROOT"}}},
                    ]
                )
                predecessors = [
                    item["run"]
                    for item in await cursor.to_list(length=None)
                    if scope(item["run"]) in wanted
                ]
            if kind == "unit":
                candidates = selected + predecessors
                enriched = []
                for offset in range(0, len(candidates), 500):
                    enriched.extend(
                        await self.unit._with_mean_scores(candidates[offset : offset + 500])
                    )
                selected, predecessors = enriched[: len(selected)], enriched[len(selected) :]
            runs.extend(selected)
            earlier.extend(predecessors)
        result = build_overview(runs, earlier)
        # Rehydrate only capped inbox rows, so response models/navigation retain full metadata.
        for kind, collection in self.collections.items():
            ids = [
                r["run_id"]
                for r in result["blocked_runs"] + result["recent_runs"]
                if r["eval_type"] == kind
            ]
            ids += [
                e["run"]["run_id"]
                for e in result["attention_runs"]
                if e["run"]["eval_type"] == kind
            ]
            full = (
                await collection.find({"eval_type": kind, "run_id": {"$in": ids}}).to_list(
                    length=None
                )
                if ids
                else []
            )
            by_id = {r["run_id"]: normalize_document(r) for r in full}
            for r in result["blocked_runs"] + result["recent_runs"]:
                if r["eval_type"] == kind and r["run_id"] in by_id:
                    r.update({k: v for k, v in by_id[r["run_id"]].items() if k != "summary"})
            for entry in result["attention_runs"]:
                r = entry["run"]
                if r["eval_type"] == kind and r["run_id"] in by_id:
                    r.update({k: v for k, v in by_id[r["run_id"]].items() if k != "summary"})
        return {
            **normalize_document(result),
            "started_from": window.started_from.isoformat(),
            "started_before": window.started_before.isoformat(),
        }

    async def facets(self, *, time_window: TimeWindow | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "unit": [],
            "e2e": [],
            "git_refs": [],
            "actors": [],
            "triggers": [],
            "pass_rate_thresholds": [],
        }
        for kind, collection in self.collections.items():
            query = {"eval_type": kind}
            apply_started_at_window(query, time_window)
            paths = {"github_repository": "github_repository"}
            paths.update(
                {
                    "skill": "skill",
                    "environment": "environment",
                    **{
                        key: "unit_config." + key
                        for key in (
                            "category",
                            "stage",
                            "target_environment",
                            "agent_name",
                            "evaluation_layer",
                        )
                    },
                }
                if kind == "unit"
                else {"stage": "stage", "target": "target"}
            )
            cursor = await collection.aggregate(
                [
                    {"$match": query},
                    {
                        "$group": {
                            "_id": {k: {"$ifNull": ["$" + v, None]} for k, v in paths.items()}
                        }
                    },
                ]
            )
            result[kind] = [
                r["_id"]
                for r in await cursor.to_list(length=None)
                if isinstance(r["_id"].get("skill" if kind == "unit" else "target"), str)
            ]
            cursor = await collection.aggregate(
                [
                    {"$match": query},
                    {
                        "$group": {
                            "_id": {
                                "github_repository": {"$ifNull": ["$github_repository", None]},
                                "git_ref": "$git_ref",
                            }
                        }
                    },
                ]
            )
            result["git_refs"].extend(
                r["_id"]
                for r in await cursor.to_list(length=None)
                if isinstance(r["_id"].get("git_ref"), str)
            )
            for output, field in (
                ("actors", "actor"),
                ("triggers", "trigger"),
                ("pass_rate_thresholds", f"{kind}_config.pass_rate_threshold"),
            ):
                result[output].extend(await collection.distinct(field, query))
        for key in ("actors", "triggers"):
            result[key] = sorted({v for v in result[key] if isinstance(v, str) and v})
        result["pass_rate_thresholds"] = sorted(
            {n for v in result["pass_rate_thresholds"] if (n := number(v)) is not None}
        )
        result["git_refs"] = list(
            {(v.get("github_repository"), v["git_ref"]): v for v in result["git_refs"]}.values()
        )
        return result
