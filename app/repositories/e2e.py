from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings
from app.repositories.base import MongoRepository


class E2ERepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]], settings: Settings) -> None:
        self.runs = MongoRepository(database[settings.e2e_runs_collection])
        self.cases = MongoRepository(database[settings.e2e_cases_collection])

    async def list_runs(
        self,
        *,
        stage: str | None,
        target: str | None,
        verdict: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {}
        if stage:
            query["stage"] = stage
        if target:
            query["target"] = target
        if verdict:
            query["verdict"] = verdict
        return await self.runs.list_by_time(query, limit=limit, cursor=cursor)

    async def list_cases(
        self,
        run_id: str,
        *,
        suite: str | None,
        verdict: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        query: dict[str, Any] = {"run_id": run_id}
        if suite:
            query["suite"] = suite
        if verdict:
            query["verdict"] = verdict
        return await self.cases.list_by_id(query, limit=limit, cursor=cursor)
