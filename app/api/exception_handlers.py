from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.exceptions import ResourceNotFoundError


async def resource_not_found_handler(
    _request: Request,
    exc: Exception,
) -> JSONResponse:
    if not isinstance(exc, ResourceNotFoundError):
        raise TypeError("Expected ResourceNotFoundError")
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": str(exc)},
    )
