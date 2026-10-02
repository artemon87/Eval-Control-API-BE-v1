from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status

from src.api.dependencies import AuthorizationServiceDependency, CurrentUserDependency
from src.security.authorization_identity import identity_from_authenticated_user
from src.security.permissions import Permission
from src.services.authorization import AuthorizationDeniedError


def require_permission(
    permission: Permission,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    async def dependency(
        current_user: CurrentUserDependency,
        service: AuthorizationServiceDependency,
    ) -> Any:
        try:
            await service.require_permission(
                identity_from_authenticated_user(current_user),
                permission,
            )
        except AuthorizationDeniedError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        return current_user

    return dependency


RunOverrideDependency = Annotated[
    Any,
    Depends(require_permission(Permission.RUN_OVERRIDE)),
]
SuggestionModerateDependency = Annotated[
    Any,
    Depends(require_permission(Permission.SUGGESTION_MODERATE)),
]
PolicyManageDependency = Annotated[
    Any,
    Depends(require_permission(Permission.POLICY_MANAGE)),
]
