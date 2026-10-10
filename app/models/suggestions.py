from typing import Literal

from pydantic import BaseModel, Field


class SuggestionAttachmentResponse(BaseModel):
    id: str
    filename: str
    content_type: Literal["image/png", "image/jpeg"]
    size_bytes: int


class SuggestionResponse(BaseModel):
    # Keep your existing fields here
    id: str
    title: str
    description: str
    author: str
    status: str
    vote_count: int
    created_at: datetime
    viewer_has_voted: bool

    attachments: list[SuggestionAttachmentResponse] = Field(
        default_factory=list
    )