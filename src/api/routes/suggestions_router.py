from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

router = APIRouter(prefix="/suggestions", tags=["suggestions"])


class SuggestionCreate(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)


class SuggestionResponse(BaseModel):
    id: str
    title: str
    description: str
    author: str
    status: Literal["open", "planned", "completed", "declined"]
    vote_count: int
    created_at: dt.datetime
    viewer_has_voted: bool


def current_user(request: Request) -> str:
    # Replace this adapter with your gateway/auth middleware's verified identity.
    user = getattr(request.state, "user", None)
    identity = getattr(user, "email", None) or getattr(user, "id", None)
    if not identity:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authenticated user required")
    return str(identity)


def database(request: Request):
    # Change only this adapter if your FastAPI app stores Mongo elsewhere.
    return request.app.state.mongodb


def serialize(document: dict, viewer_has_voted: bool) -> SuggestionResponse:
    return SuggestionResponse(
        id=str(document["_id"]),
        title=document["title"],
        description=document["description"],
        author=document["author"],
        status=document.get("status", "open"),
        vote_count=document.get("vote_count", 0),
        created_at=document["created_at"],
        viewer_has_voted=viewer_has_voted,
    )


@router.get("")
async def list_suggestions(
    request: Request,
    viewer: Annotated[str, Depends(current_user)],
    limit: int = 100,
) -> dict[str, list[SuggestionResponse]]:
    db = database(request)
    documents = await db.suggestions.find({}).sort([("vote_count", -1), ("created_at", -1)]).limit(min(limit, 100)).to_list(length=min(limit, 100))
    ids = [document["_id"] for document in documents]
    votes = await db.suggestion_votes.find({"suggestion_id": {"$in": ids}, "user_id": viewer}).to_list(length=len(ids))
    voted_ids = {vote["suggestion_id"] for vote in votes}
    return {"items": [serialize(document, document["_id"] in voted_ids) for document in documents]}


@router.post("", response_model=SuggestionResponse, status_code=status.HTTP_201_CREATED)
async def create_suggestion(
    payload: SuggestionCreate,
    request: Request,
    viewer: Annotated[str, Depends(current_user)],
) -> SuggestionResponse:
    now = dt.datetime.now(dt.UTC)
    document = {"title": payload.title.strip(), "description": payload.description.strip(), "author": viewer, "status": "open", "vote_count": 0, "created_at": now, "updated_at": now}
    result = await database(request).suggestions.insert_one(document)
    document["_id"] = result.inserted_id
    return serialize(document, False)


@router.put("/{suggestion_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def add_vote(suggestion_id: str, request: Request, viewer: Annotated[str, Depends(current_user)]) -> None:
    if not ObjectId.is_valid(suggestion_id):
        raise HTTPException(status_code=404, detail="Suggestion not found")
    db = database(request)
    object_id = ObjectId(suggestion_id)
    if not await db.suggestions.find_one({"_id": object_id}, {"_id": 1}):
        raise HTTPException(status_code=404, detail="Suggestion not found")
    try:
        await db.suggestion_votes.insert_one({"suggestion_id": object_id, "user_id": viewer, "created_at": dt.datetime.now(dt.UTC)})
    except DuplicateKeyError:
        return
    await db.suggestions.find_one_and_update({"_id": object_id}, {"$inc": {"vote_count": 1}}, return_document=ReturnDocument.AFTER)


@router.delete("/{suggestion_id}/vote", status_code=status.HTTP_204_NO_CONTENT)
async def remove_vote(suggestion_id: str, request: Request, viewer: Annotated[str, Depends(current_user)]) -> None:
    if not ObjectId.is_valid(suggestion_id):
        raise HTTPException(status_code=404, detail="Suggestion not found")
    db = database(request)
    object_id = ObjectId(suggestion_id)
    deleted = await db.suggestion_votes.delete_one({"suggestion_id": object_id, "user_id": viewer})
    if deleted.deleted_count:
        await db.suggestions.update_one({"_id": object_id, "vote_count": {"$gt": 0}}, {"$inc": {"vote_count": -1}})


async def ensure_suggestion_indexes(db) -> None:
    await db.suggestions.create_index([("vote_count", -1), ("created_at", -1)], name="suggestion_priority")
    await db.suggestion_votes.create_index([("suggestion_id", 1), ("user_id", 1)], unique=True, name="one_vote_per_user")
