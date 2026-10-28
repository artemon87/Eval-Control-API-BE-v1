from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


ProductUpdateStatus = Literal["shipped", "coming", "planned"]


class ProductUpdateImage(BaseModel):
    id: str
    filename: str
    content_type: Literal["image/png", "image/jpeg"]
    size_bytes: int


class ProductUpdateResponse(BaseModel):
    id: str
    status: ProductUpdateStatus
    release_date: date
    category: str
    title: str
    description: str
    highlights: list[str] = Field(default_factory=list)
    image: ProductUpdateImage | None = None
    created_at: datetime
    updated_at: datetime


class ProductUpdateListResponse(BaseModel):
    items: list[ProductUpdateResponse]
