from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings
from app.models import GitFilters
from app.repositories.base import MongoRepository, apply_git_filters, normalize_document


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
                "$group": {
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
                "$group": {
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
        git_filters: GitFilters | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"eval_type": "unit"}
        if skill:
            query["skill"] = skill
        if environment:
            query["environment"] = environment
        if stage:
            query["unit_config.stage"] = (
                stage[0] if len(stage) == 1 else {"$in": stage}
            )
        if target_environment:
            query["unit_config.target_environment"] = target_environment
        if category:
            query["unit_config.category"] = category
        if agent_name:
            query["unit_config.agent_name"] = agent_name
        if skill_version:
            query["unit_config.skill_version"] = skill_version
        if verdict:
            query["verdict"] = verdict
        if git_filters is not None:
            apply_git_filters(
                query,
                git_filters.github_repository,
                git_filters.git_ref,
                git_filters.git_sha,
            )
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
        git_filters: GitFilters | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        run_query: dict[str, Any] = {
            "eval_type": "unit",
            "skill": skill,
            "execution_status": "completed",
            "started_at": {"$ne": None},
        }
        if environment:
            run_query["environment"] = environment
        if stage:
            run_query["unit_config.stage"] = stage
        if target_environment:
            run_query["unit_config.target_environment"] = target_environment
        if category:
            run_query["unit_config.category"] = category
        if git_filters is not None:
            apply_git_filters(
                run_query,
                git_filters.github_repository,
                git_filters.git_ref,
                git_filters.git_sha,
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

        points: list[dict[str, Any]] = []
        for run in runs:
            cases = cases_by_run.get(run["run_id"], [])
            if not cases:
                continue
            scores = [
                float(score)
                for case in cases
                for score in case.get("scores", {}).values()
                if score is not None
            ]
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
                    "score": _mean(scores),
                    "threshold": 4.0,
                    "pass_rate_pct": passed / len(cases) * 100,
                    "total_cases": len(cases),
                    "skill_version": config.get("skill_version"),
                    "bsa_version": config.get("bsa_version"),
                }
            )
        return points, next_cursor

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
            match["unit_config.stage"] = stage
        if target_environment:
            match["unit_config.target_environment"] = target_environment
        pipeline: list[dict[str, Any]] = [
            {"$match": match},
            {"$sort": {"started_at": -1, "_id": -1}},
            {"$group": {"_id": "$unit_config.skill_version", "run": {"$first": "$$ROOT"}}},
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
            query["unit_config.stage"] = stage
        if target_environment:
            query["unit_config.target_environment"] = target_environment
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
