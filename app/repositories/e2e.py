from typing import Any

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
        query: dict[str, Any] = {"eval_type": "e2e"}
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
        query: dict[str, Any] = {"run_id": run_id, "eval_type": "e2e"}
        if suite:
            query["suite"] = suite
        if verdict:
            query["verdict"] = verdict
        return await self.cases.list_by_id(query, limit=limit, cursor=cursor)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return await self.runs.get_by_run_id(run_id, eval_type="e2e")

    async def trend(
        self,
        *,
        scope: str,
        value: str,
        stage: str | None,
        target: str | None,
        suite: str | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        run_query: dict[str, Any] = {
            "eval_type": "e2e",
            "execution_status": "completed",
            "started_at": {"$ne": None},
        }
        if stage:
            run_query["stage"] = stage
        if target:
            run_query["target"] = target
        runs, next_cursor = await self.runs.list_by_time(
            run_query, limit=limit, cursor=cursor
        )
        if not runs:
            return [], next_cursor

        case_query: dict[str, Any] = {
            "eval_type": "e2e",
            "run_id": {"$in": [run["run_id"] for run in runs]},
        }
        if scope == "case":
            case_query["case_id"] = value
            if suite:
                case_query["suite"] = suite
        else:
            case_query["suite"] = value
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
            scores = [float(case["score"]) for case in cases if case.get("score") is not None]
            thresholds = [
                float(case["threshold"]) for case in cases if case.get("threshold") is not None
            ]
            latencies = [
                float(case["response_time_ms"])
                for case in cases
                if case.get("response_time_ms") is not None
            ]
            passed = sum(
                str(case.get("verdict", "")).lower() in {"passed", "xpassed"}
                for case in cases
            )
            points.append(
                {
                    "run_id": run["run_id"],
                    "started_at": run["started_at"],
                    "verdict": _case_verdict(cases),
                    "score": _mean(scores),
                    "threshold": _mean(thresholds),
                    "pass_rate_pct": passed / len(cases) * 100,
                    "total_cases": len(cases),
                    "response_time_ms": _mean(latencies),
                }
            )
        return points, next_cursor
