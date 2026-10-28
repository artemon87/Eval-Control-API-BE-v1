from fastapi import Request, status
from fastapi.responses import JSONResponse


async def product_update_not_found_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )
