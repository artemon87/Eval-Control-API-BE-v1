import asyncio

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, IndexModel

from app.config import get_settings


async def main() -> None:
    settings = get_settings()
    client = AsyncMongoClient(settings.mongodb_uri, appname="eval-control-indexer")
    database = client[settings.mongodb_database]
    try:
        await database[settings.unit_runs_collection].create_indexes(
            [
                IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
                IndexModel(
                    [
                        ("skill", ASCENDING),
                        ("environment", ASCENDING),
                        ("unit_config.skill_version", ASCENDING),
                        ("started_at", DESCENDING),
                        ("_id", DESCENDING),
                    ],
                    name="skill_version_history",
                ),
                IndexModel(
                    [("verdict", ASCENDING), ("started_at", DESCENDING), ("_id", DESCENDING)],
                    name="verdict_history",
                ),
            ]
        )
        await database[settings.unit_cases_collection].create_indexes(
            [
                IndexModel([("run_id", ASCENDING), ("_id", DESCENDING)], name="run_cases"),
                IndexModel([("case_id", ASCENDING), ("run_id", ASCENDING)], name="case_history"),
            ]
        )
        await database[settings.e2e_runs_collection].create_indexes(
            [
                IndexModel([("run_id", ASCENDING)], unique=True, name="run_id_unique"),
                IndexModel(
                    [
                        ("stage", ASCENDING),
                        ("target", ASCENDING),
                        ("started_at", DESCENDING),
                        ("_id", DESCENDING),
                    ],
                    name="target_history",
                ),
                IndexModel(
                    [("batch_id", ASCENDING), ("started_at", DESCENDING)],
                    name="batch_history",
                ),
            ]
        )
        await database[settings.e2e_cases_collection].create_indexes(
            [
                IndexModel([("run_id", ASCENDING), ("_id", DESCENDING)], name="run_cases"),
                IndexModel(
                    [("suite", ASCENDING), ("verdict", ASCENDING), ("_id", DESCENDING)],
                    name="suite_verdict",
                ),
            ]
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
