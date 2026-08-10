from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings
from app.repositories.base import MongoRepository, normalize_document


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
        query: dict[str, Any] = {}
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
        query: dict[str, Any] = {"run_id": run_id}
        if verdict:
            query["verdict"] = verdict
        return await self.cases.list_by_id(query, limit=limit, cursor=cursor)

    async def latest_runs_by_version(
        self, skill: str, environment: str | None, limit: int = 20
    ) -> list[dict[str, Any]]:
        match: dict[str, Any] = {"skill": skill, "execution_status": "completed"}
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
        documents = await self.cases.collection.find({"run_id": {"$in": run_ids}}).to_list(
            length=None
        )
        return [normalize_document(document) for document in documents]
