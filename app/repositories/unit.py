from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings
from app.repositories.base import MongoRepository, normalize_document

DEFAULT_BEHAVIORAL_THRESHOLD = 1.0
DEFAULT_METRIC_THRESHOLD = 4.0
MONGO_GROUP_OPERATOR = "$group"
UNIT_STAGE_FIELD = "unit_config.stage"
UNIT_TARGET_ENVIRONMENT_FIELD = "unit_config.target_environment"


def _case_verdict(cases: list[dict[str, Any]]) -> str:
    verdicts = {str(case.get("verdict", "failed")).lower() for case in cases}
    if verdicts & {"blocked", "error"}:
        return "blocked"
    if "failed" in verdicts:
        return "failed"
    if "xpassed" in verdicts:
        return "xpassed"
    return "passed"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _trend_run_query(
    *,
    skill: str,
    environment: str | None,
    stage: str | None,
    target_environment: str | None,
    category: str | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "eval_type": "unit",
        "skill": skill,
        "execution_status": "completed",
        "started_at": {"$ne": None},
    }
    optional_filters = {
        "environment": environment,
        UNIT_STAGE_FIELD: stage,
        UNIT_TARGET_ENVIRONMENT_FIELD: target_environment,
        "unit_config.category": category,
    }
    query.update({field: value for field, value in optional_filters.items() if value})
    return query


def _normalized_rate(value: Any) -> float | None:
    if value is None:
        return None
    rate = float(value)
    return rate / 100 if rate > 1 else rate


def _case_metric_values(
    cases: list[dict[str, Any]],
    field: str,
    metric: str | None,
) -> list[float]:
    if metric is not None:
        return [
            float(values[metric])
            for case in cases
            if (values := case.get(field) or {})
            and metric in values
            and values[metric] is not None
        ]
    return [
        float(value)
        for case in cases
        for value in (case.get(field) or {}).values()
        if value is not None
    ]


def _behavioral_case_value(case: dict[str, Any]) -> float:
    pass_rate = _normalized_rate(case.get("pass_rate"))
    if pass_rate is not None:
        return pass_rate

    score_values = [
        float(value)
        for value in (case.get("scores") or {}).values()
        if value is not None
    ]
    average_score = _mean(score_values)
    if average_score is not None:
        return average_score

    verdict = str(case.get("verdict") or "").lower()
    return 1.0 if verdict in {"passed", "xpassed"} else 0.0


def _behavioral_threshold(
    cases: list[dict[str, Any]],
    config: dict[str, Any],
) -> float:
    configured_threshold = _normalized_rate(config.get("consistency_threshold"))
    if configured_threshold is not None:
        return configured_threshold

    stored_thresholds = _case_metric_values(cases, "thresholds", None)
    stored_threshold = _mean(stored_thresholds)
    return (
        stored_threshold
        if stored_threshold is not None
        else DEFAULT_BEHAVIORAL_THRESHOLD
    )


def _case_trend_values(
    cases: list[dict[str, Any]],
    run: dict[str, Any],
    metric: str | None = None,
) -> tuple[float, float]:
    config = run.get("unit_config") or {}
    evaluation_layer = (
        str(config.get("evaluation_layer") or "").lower().replace("_", "-")
    )
    if evaluation_layer == "behavioral-contracts":
        case_values = [_behavioral_case_value(case) for case in cases]
        score = _mean(case_values)
        return (
            score if score is not None else 0.0,
            _behavioral_threshold(cases, config),
        )

    scores = _case_metric_values(cases, "scores", metric)
    thresholds = _case_metric_values(cases, "thresholds", metric)
    average_score = _mean(scores)
    average_threshold = _mean(thresholds)
    return (
        average_score if average_score is not None else 0.0,
        average_threshold
        if average_threshold is not None
        else DEFAULT_METRIC_THRESHOLD,
    )


def _build_trend_points(
    runs: list[dict[str, Any]], cases_by_run: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for run in runs:
        cases = cases_by_run.get(run["run_id"], [])
        if not cases:
            continue
        score, threshold = _case_trend_values(cases, run)
        passed = sum(
            str(case.get("verdict", "")).lower() in {"passed", "xpassed"}
            for case in cases
        )
        config = run.get("unit_config", {})
        points.append(
            {
                "run_id": run["run_id"],
                "started_at": run["started_at"],
                "verdict": _case_verdict(cases),
                "score": score,
                "threshold": threshold,
                "pass_rate_pct": passed / len(cases) * 100,
                "total_cases": len(cases),
                "skill_version": config.get("skill_version"),
                "bsa_version": config.get("bsa_version"),
            }
        )
    return points

class UnitRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]], settings: Settings) -> None:
        self.runs = MongoRepository(database[settings.unit_runs_collection])
        self.cases = MongoRepository(database[settings.unit_cases_collection])

    async def _with_mean_scores(
        self, runs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Add each run's mean metric score without loading full case records."""
        run_ids = [str(run["run_id"]) for run in runs]
        if not run_ids:
            return runs

        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "eval_type": "unit",
                    "run_id": {"$in": run_ids},
                }
            },
            {
                "$project": {
                    "run_id": 1,
                    "scores": {
                        "$objectToArray": {"$ifNull": ["$scores", {}]},
                    },
                }
            },
            {"$unwind": "$scores"},
            {
                MONGO_GROUP_OPERATOR: {
                    "_id": {
                        "run_id": "$run_id",
                        "metric": "$scores.k",
                    },
                    "mean_score": {"$avg": "$scores.v"},
                    "score_total": {"$sum": "$scores.v"},
                    "score_count": {"$sum": 1},
                }
            },
            {
                MONGO_GROUP_OPERATOR: {
                    "_id": "$_id.run_id",
                    "score_total": {"$sum": "$score_total"},
                    "score_count": {"$sum": "$score_count"},
                    "mean_scores": {
                        "$push": {
                            "k": "$_id.metric",
                            "v": "$mean_score",
                        }
                    },
                }
            },
            {
                "$project": {
                    "mean_score": {
                        "$divide": ["$score_total", "$score_count"],
                    },
                    "mean_scores": {"$arrayToObject": "$mean_scores"},
                }
            },
        ]
        cursor = await self.cases.collection.aggregate(pipeline)
        aggregates = await cursor.to_list(length=None)
        means: dict[str, tuple[float, dict[str, float]]] = {
            str(item["_id"]): (
                float(item["mean_score"]),
                {
                    str(metric): float(score)
                    for metric, score in item.get("mean_scores", {}).items()
                },
            )
            for item in aggregates
            if item.get("mean_score") is not None
        }

        enriched: list[dict[str, Any]] = []
        for run in runs:
            item = dict(run)
            summary = dict(item.get("summary", {}))
            aggregate = means.get(str(item["run_id"]))
            summary["mean_score"] = aggregate[0] if aggregate is not None else None
            summary["mean_scores"] = aggregate[1] if aggregate is not None else {}
            item["summary"] = summary
            enriched.append(item)
        return enriched

    async def list_runs(
        self,
        *,
        skill: str | None,
        environment: str | None,
        stage: list[str] | None,
        target_environment: str | None,
        category: str | None,
        agent_name: str | None,
        skill_version: str | None,
        verdict: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"eval_type": "unit"}
        if skill:
            query["skill"] = skill
        if environment:
            query["environment"] = environment
        if stage:
            query[UNIT_STAGE_FIELD] = (
                stage[0] if len(stage) == 1 else {"$in": stage}
            )
        if target_environment:
            query[UNIT_TARGET_ENVIRONMENT_FIELD] = target_environment
        if category:
            query["unit_config.category"] = category
        if agent_name:
            query["unit_config.agent_name"] = agent_name
        if skill_version:
            query["unit_config.skill_version"] = skill_version
        if verdict:
            query["verdict"] = verdict
        runs, next_cursor = await self.runs.list_by_time(
            query, limit=limit, cursor=cursor
        )
        return await self._with_mean_scores(runs), next_cursor

    async def list_cases(
        self,
        run_id: str,
        *,
        verdict: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"run_id": run_id, "eval_type": "unit"}
        if verdict:
            query["verdict"] = verdict
        return await self.cases.list_by_id(query, limit=limit, cursor=cursor)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = await self.runs.get_by_run_id(run_id, eval_type="unit")
        if not run:
            return None
        return (await self._with_mean_scores([run]))[0]

    async def trend(
        self,
        *,
        scope: str,
        skill: str,
        case_id: str | None,
        environment: str | None,
        stage: str | None,
        target_environment: str | None,
        category: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        run_query = _trend_run_query(
            skill=skill,
            environment=environment,
            stage=stage,
            target_environment=target_environment,
            category=category,
        )
        runs, next_cursor = await self.runs.list_by_time(
            run_query, limit=limit, cursor=cursor
        )
        if not runs:
            return [], next_cursor

        case_query: dict[str, Any] = {
            "eval_type": "unit",
            "skill": skill,
            "run_id": {"$in": [run["run_id"] for run in runs]},
        }
        if scope == "case" and case_id:
            case_query["case_id"] = case_id
        documents = await self.cases.collection.find(case_query).to_list(length=None)
        cases_by_run: dict[str, list[dict[str, Any]]] = {}
        for document in documents:
            case = normalize_document(document)
            cases_by_run.setdefault(case["run_id"], []).append(case)

        return _build_trend_points(runs, cases_by_run), next_cursor

    async def latest_runs_by_version(
        self,
        skill: str,
        environment: str | None,
        stage: str | None = None,
        target_environment: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        match: dict[str, Any] = {
            "eval_type": "unit",
            "skill": skill,
            "execution_status": "completed",
        }
        if environment:
            match["environment"] = environment
        if stage:
            match[UNIT_STAGE_FIELD] = stage
        if target_environment:
            match[UNIT_TARGET_ENVIRONMENT_FIELD] = target_environment
        pipeline: list[dict[str, Any]] = [
            {"$match": match},
            {"$sort": {"started_at": -1, "_id": -1}},
            {
                MONGO_GROUP_OPERATOR: {
                    "_id": "$unit_config.skill_version",
                    "run": {"$first": "$$ROOT"},
                }
            },
            {"$sort": {"run.started_at": -1, "run._id": -1}},
            {"$limit": limit},
            {"$replaceRoot": {"newRoot": "$run"}},
        ]
        cursor = await self.runs.collection.aggregate(pipeline)
        documents = await cursor.to_list(length=limit)
        return [normalize_document(document) for document in documents]

    async def latest_run_for_version(
        self,
        skill: str,
        version: str,
        environment: str | None,
        stage: str | None = None,
        target_environment: str | None = None,
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {
            "eval_type": "unit",
            "skill": skill,
            "unit_config.skill_version": version,
            "execution_status": "completed",
        }
        if environment:
            query["environment"] = environment
        if stage:
            query[UNIT_STAGE_FIELD] = stage
        if target_environment:
            query[UNIT_TARGET_ENVIRONMENT_FIELD] = target_environment
        document = await self.runs.collection.find_one(
            query, sort=[("started_at", DESCENDING), ("_id", DESCENDING)]
        )
        return normalize_document(document) if document else None

    async def cases_for_runs(self, run_ids: list[str]) -> list[dict[str, Any]]:
        documents = await self.cases.collection.find({
            "eval_type": "unit", "run_id": {"$in": run_ids}
        }).to_list(
            length=None
        )
        return [normalize_document(document) for document in documents]
