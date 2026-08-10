import base64
import json
from datetime import UTC, datetime
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.asynchronous.collection import AsyncCollection


def normalize_document(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: normalize_document(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_document(item) for item in value]
    return value


def encode_cursor(sort_value: datetime, object_id: ObjectId) -> str:
    payload = json.dumps({"at": sort_value.astimezone(UTC).isoformat(), "id": str(object_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, ObjectId]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode())
        return datetime.fromisoformat(payload["at"]), ObjectId(payload["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc


def encode_id_cursor(object_id: ObjectId) -> str:
    return base64.urlsafe_b64encode(str(object_id).encode()).decode().rstrip("=")


def decode_id_cursor(cursor: str) -> ObjectId:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return ObjectId(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid cursor") from exc


class MongoRepository:
    def __init__(self, collection: AsyncCollection[dict[str, Any]]) -> None:
        self.collection = collection

    async def list_by_time(
        self,
        query: dict[str, Any],
        *,
        limit: int,
        cursor: str | None,
        sort_field: str = "started_at",
    ) -> tuple[list[dict[str, Any]], str | None]:
        effective_query = dict(query)
        if cursor:
            at, object_id = decode_cursor(cursor)
            effective_query["$or"] = [
                {sort_field: {"$lt": at}},
                {sort_field: at, "_id": {"$lt": object_id}},
            ]

        mongo_cursor = (
            self.collection.find(effective_query)
            .sort([(sort_field, DESCENDING), ("_id", DESCENDING)])
            .limit(limit + 1)
        )
        documents = await mongo_cursor.to_list(length=limit + 1)
        has_more = len(documents) > limit
        page = documents[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor(last[sort_field], last["_id"])
        return [normalize_document(document) for document in page], next_cursor

    async def list_by_id(
        self,
        query: dict[str, Any],
        *,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        effective_query = dict(query)
        if cursor:
            effective_query["_id"] = {"$lt": decode_id_cursor(cursor)}

        mongo_cursor = (
            self.collection.find(effective_query).sort("_id", DESCENDING).limit(limit + 1)
        )
        documents = await mongo_cursor.to_list(length=limit + 1)
        has_more = len(documents) > limit
        page = documents[:limit]
        next_cursor = encode_id_cursor(page[-1]["_id"]) if has_more and page else None
        return [normalize_document(document) for document in page], next_cursor

    async def get_by_run_id(self, run_id: str) -> dict[str, Any] | None:
        document = await self.collection.find_one({"run_id": run_id})
        return normalize_document(document) if document else None
