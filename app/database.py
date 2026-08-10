from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings


@asynccontextmanager
async def database_lifespan(app: FastAPI, settings: Settings) -> AsyncIterator[None]:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.mongodb_uri,
        appname="eval-control-api",
        maxPoolSize=settings.mongodb_max_pool_size,
        minPoolSize=settings.mongodb_min_pool_size,
        maxIdleTimeMS=60_000,
        connectTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
        retryReads=True,
        uuidRepresentation="standard",
    )
    app.state.mongo_client = client
    app.state.database = client[settings.mongodb_database]
    try:
        yield
    finally:
        await client.close()


def get_database(app: FastAPI) -> AsyncDatabase[dict[str, object]]:
    return cast(AsyncDatabase[dict[str, object]], app.state.database)
