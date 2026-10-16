from __future__ import annotations

import datetime as dt
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from app.config import Settings


PRODUCT_UPDATE_NOT_FOUND = "Product update not found"


class ProductUpdateRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]], settings: Settings) -> None:
        self.collection = database[settings.product_updates_collection]

    async def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        effective_limit = min(max(limit, 1), 100)
        cursor = self.collection.find({}).sort(
            [("release_date", DESCENDING), ("created_at", DESCENDING)]
        ).limit(effective_limit)
        return [self._serialize(item) for item in await cursor.to_list(length=effective_limit)]

    async def create(
        self,
        *,
        status: str,
        release_date: dt.date,
        category: str,
        title: str,
        description: str,
        highlights: list[str],
        image: dict[str, object] | None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        document: dict[str, Any] = {
            "status": status,
            "release_date": release_date.isoformat(),
            "category": category.strip(),
            "title": title.strip(),
            "description": description.strip(),
            "highlights": [item.strip() for item in highlights if item.strip()],
            "image": image,
            "created_at": now,
            "updated_at": now,
        }
        result = await self.collection.insert_one(document)
        document["_id"] = result.inserted_id
        return self._serialize(document)

    async def update(self, update_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        object_id = self._object_id(update_id)
        changes["updated_at"] = dt.datetime.now(dt.UTC)
        document = await self.collection.find_one_and_update(
            {"_id": object_id},
            {"$set": changes},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise LookupError(PRODUCT_UPDATE_NOT_FOUND)
        return self._serialize(document)

    async def delete(self, update_id: str) -> dict[str, Any]:
        document = await self.collection.find_one_and_delete({"_id": self._object_id(update_id)})
        if document is None:
            raise LookupError(PRODUCT_UPDATE_NOT_FOUND)
        return document

    async def get_image(self, update_id: str) -> dict[str, Any]:
        document = await self.collection.find_one({"_id": self._object_id(update_id)})
        if document is None or not document.get("image"):
            raise LookupError(PRODUCT_UPDATE_NOT_FOUND)
        return document["image"]

    @staticmethod
    def _object_id(value: str) -> ObjectId:
        if not ObjectId.is_valid(value):
            raise LookupError(PRODUCT_UPDATE_NOT_FOUND)
        return ObjectId(value)

    @staticmethod
    def _serialize(document: dict[str, Any]) -> dict[str, Any]:
        image = document.get("image")
        return {
            "id": str(document["_id"]),
            "status": document["status"],
            "release_date": document["release_date"],
            "category": document["category"],
            "title": document["title"],
            "description": document["description"],
            "highlights": document.get("highlights", []),
            "image": ({key: image[key] for key in ("id", "filename", "content_type", "size_bytes")} if image else None),
            "created_at": document["created_at"],
            "updated_at": document["updated_at"],
        }
