from typing import cast

import pytest
from fastapi import Request

from app.api.exception_handlers import resource_not_found_handler
from app.exceptions import ResourceNotFoundError
from app.repositories.product_updates import ProductUpdateNotFoundError, ProductUpdateRepository


async def test_resource_not_found_handler_returns_404_response() -> None:
    response = await resource_not_found_handler(
        cast(Request, None),
        ResourceNotFoundError("Resource not found"),
    )

    assert response.status_code == 404
    assert response.body == b'{"detail":"Resource not found"}'


async def test_resource_not_found_handler_rejects_unexpected_exception() -> None:
    with pytest.raises(TypeError, match="Expected ResourceNotFoundError"):
        await resource_not_found_handler(cast(Request, None), RuntimeError("unexpected"))


def test_product_update_repository_raises_specific_not_found_error() -> None:
    with pytest.raises(ProductUpdateNotFoundError, match="Product update not found"):
        ProductUpdateRepository._object_id("not-an-object-id")
