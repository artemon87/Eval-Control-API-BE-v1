from __future__ import annotations

import datetime as dt
from typing import Any

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from src.config import Settings
from src.repositories.base import MongoRepository, normalize_document

SUGGESTION_NOT_FOUND: str = "Suggestion not found"


class SuggestionRepository:
    def __init__(self, database: AsyncDatabase[dict[str, Any]], settings: Settings) -> None:
        self.suggestions = MongoRepository(database[settings.suggestions_collection])
        self.votes = MongoRepository(database[settings.suggestion_votes_collection])

    async def list(self, *, viewer: str, limit: int) -> list[dict[str, Any]]:
        effective_limit = min(limit, 100)
        cursor = (
            self.suggestions.collection.find({})
            .sort(
                [
                    ("vote_count", DESCENDING),
                    ("created_at", DESCENDING),
                ]
            )
            .limit(effective_limit)
        )
        documents = await cursor.to_list(length=effective_limit)
        if not documents:
            return []

        suggestion_ids = [document["_id"] for document in documents]
        vote_cursor = self.votes.collection.find(
            {
                "suggestion_id": {"$in": suggestion_ids},
                "user_id": viewer,
            }
        )
        votes = await vote_cursor.to_list(length=len(suggestion_ids))
        voted_ids = {vote["suggestion_id"] for vote in votes}
        return [
            self._serialize(
                document,
                viewer_has_voted=document["_id"] in voted_ids,
            )
            for document in documents
        ]

    async def create(
        self,
        *,
        title: str,
        description: str,
        author: str,
        attachments: list[dict[str, object]] | None = None,
        suggestion_id: ObjectId | None = None,
    ) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        document: dict[str, Any] = {
            "_id": suggestion_id or ObjectId(),
            "title": title.strip(),
            "description": description.strip(),
            "author": author,
            "status": "open",
            "vote_count": 0,
            "attachments": attachments or [],
            "created_at": now,
            "updated_at": now,
        }
        await self.suggestions.collection.insert_one(document)
        return self._serialize(document, viewer_has_voted=False)

    async def get_attachment(
        self,
        *,
        suggestion_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        object_id = self._object_id(suggestion_id)
        document = await self.suggestions.collection.find_one(
            {"_id": object_id, "attachments.id": attachment_id},
            {"attachments.$": 1},
        )
        if not document or not document.get("attachments"):
            raise LookupError(SUGGESTION_NOT_FOUND)
        return document["attachments"][0]

    async def add_vote(self, *, suggestion_id: str, user_id: str) -> bool:
        object_id = self._object_id(suggestion_id)
        suggestion = await self.suggestions.collection.find_one(
            {"_id": object_id},
            {"_id": 1},
        )
        if suggestion is None:
            raise LookupError(SUGGESTION_NOT_FOUND)

        try:
            await self.votes.collection.insert_one(
                {
                    "suggestion_id": object_id,
                    "user_id": user_id,
                    "created_at": dt.datetime.now(dt.UTC),
                }
            )
        except DuplicateKeyError:
            return False

        await self.suggestions.collection.update_one(
            {"_id": object_id},
            {
                "$inc": {"vote_count": 1},
                "$set": {"updated_at": dt.datetime.now(dt.UTC)},
            },
        )
        return True

    async def remove_vote(self, *, suggestion_id: str, user_id: str) -> bool:
        object_id = self._object_id(suggestion_id)
        suggestion = await self.suggestions.collection.find_one(
            {"_id": object_id},
            {"_id": 1},
        )
        if suggestion is None:
            raise LookupError(SUGGESTION_NOT_FOUND)

        result = await self.votes.collection.delete_one(
            {"suggestion_id": object_id, "user_id": user_id}
        )
        if result.deleted_count == 0:
            return False

        await self.suggestions.collection.update_one(
            {"_id": object_id, "vote_count": {"$gt": 0}},
            {
                "$inc": {"vote_count": -1},
                "$set": {"updated_at": dt.datetime.now(dt.UTC)},
            },
        )
        return True

    @staticmethod
    def _object_id(value: str) -> ObjectId:
        if not ObjectId.is_valid(value):
            raise LookupError(SUGGESTION_NOT_FOUND)
        return ObjectId(value)

    @staticmethod
    def _serialize(
        document: dict[str, Any],
        *,
        viewer_has_voted: bool,
    ) -> dict[str, Any]:
        normalized = normalize_document(document)
        attachments = normalized.get("attachments", [])
        return {
            "id": normalized["_id"],
            "title": normalized["title"],
            "description": normalized["description"],
            "author": normalized["author"],
            "status": normalized.get("status", "open"),
            "vote_count": normalized.get("vote_count", 0),
            "created_at": normalized["created_at"],
            "attachments": [
                {
                    "id": attachment["id"],
                    "filename": attachment["filename"],
                    "content_type": attachment["content_type"],
                    "size_bytes": attachment["size_bytes"],
                }
                for attachment in attachments
            ],
            "viewer_has_voted": viewer_has_voted,
        }
