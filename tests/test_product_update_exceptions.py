from typing import cast

import pytest
from fastapi import Request

from app.api.exception_handlers import product_update_not_found_handler
from app.repositories.product_updates import (
    ProductUpdateNotFoundError,
    ProductUpdateRepository,
)


@pytest.mark.asyncio
async def test_product_update_not_found_handler_returns_404() -> None:
    response = await product_update_not_found_handler(
        cast(Request, None),
        ProductUpdateNotFoundError("Product update not found"),
    )

    assert response.status_code == 404
    assert response.body == b'{"detail":"Product update not found"}'


def test_invalid_product_update_id_raises_specific_exception() -> None:
    with pytest.raises(ProductUpdateNotFoundError, match="Product update not found"):
        ProductUpdateRepository._object_id("not-an-object-id")
