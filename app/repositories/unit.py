from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings
from app.repositories.base import MongoRepository, normalize_document


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

    async def list_runs(
        self,
        *,
        skill: str | None,
        environment: str | None,
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
        if skill_version:
            query["unit_config.skill_version"] = skill_version
        if verdict:
            query["verdict"] = verdict
        return await self.runs.list_by_time(query, limit=limit, cursor=cursor)

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
        return await self.runs.get_by_run_id(run_id, eval_type="unit")

    async def trend(
        self,
        *,
        scope: str,
        skill: str,
        case_id: str | None,
        environment: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        run_query: dict[str, Any] = {
            "eval_type": "unit",
            "skill": skill,
            "execution_status": "completed",
            "started_at": {"$ne": None},
        }
        if environment:
            run_query["environment"] = environment
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
        self, skill: str, environment: str | None, limit: int = 20
    ) -> list[dict[str, Any]]:
        match: dict[str, Any] = {
            "eval_type": "unit",
            "skill": skill,
            "execution_status": "completed",
        }
        if environment:
            match["environment"] = environment
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
        self, skill: str, version: str, environment: str | None
    ) -> dict[str, Any] | None:
        query: dict[str, Any] = {
            "eval_type": "unit",
            "skill": skill,
            "unit_config.skill_version": version,
            "execution_status": "completed",
        }
        if environment:
            query["environment"] = environment
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
