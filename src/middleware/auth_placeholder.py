from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class AuthenticationPlaceholderMiddleware(BaseHTTPMiddleware):
    """Extension point for SSO/OIDC without coupling routes to an auth vendor."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.principal = None
        return await call_next(request)
