from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, status
from src.api.dependencies import AuthorizationServiceDependency, CurrentUserDependency

from src.models.authorization import ResourceContext
from src.security.authorization_identity import identity_from_authenticated_user
from src.security.permissions import Permission, ResourceType
from src.security.resources import evaluation_resource
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


def require_resource_permission(
    permission: Permission,
    resource: ResourceType,
    *,
    attributes: dict[str, str] | None = None,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build a dependency for a resource whose attributes are known by the route.

    Use AuthorizationService.require_resource_permission directly in the domain
    service when attributes such as category or subgroup are only known after
    loading the stored evaluation.
    """
    resource_context = ResourceContext(
        resource=resource,
        attributes=attributes or {},
    )

    async def dependency(
        current_user: CurrentUserDependency,
        service: AuthorizationServiceDependency,
    ) -> Any:
        try:
            await service.require_resource_permission(
                identity_from_authenticated_user(current_user),
                permission,
                resource_context,
            )
        except AuthorizationDeniedError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            ) from exc
        return current_user

    return dependency


EvalHubAdminDependency = Annotated[
    Any,
    Depends(require_permission(Permission.ACCESS_MANAGE)),
]

CanEditE2EEvaluations = Annotated[
    Any,
    Depends(
        require_resource_permission(
            Permission.EVAL_EDIT,
            ResourceType.EVALUATION,
            attributes=evaluation_resource("e2e").attributes,
        )
    ),
]

CanAnnotateE2EEvaluations = Annotated[
    Any,
    Depends(
        require_resource_permission(
            Permission.EVAL_ANNOTATE,
            ResourceType.EVALUATION,
            attributes=evaluation_resource("e2e").attributes,
        )
    ),
]

CanEditUnitEvaluations = Annotated[
    Any,
    Depends(
        require_resource_permission(
            Permission.EVAL_EDIT,
            ResourceType.EVALUATION,
            attributes=evaluation_resource("unit").attributes,
        )
    ),
]

CanAnnotateUnitEvaluations = Annotated[
    Any,
    Depends(
        require_resource_permission(
            Permission.EVAL_ANNOTATE,
            ResourceType.EVALUATION,
            attributes=evaluation_resource("unit").attributes,
        )
    ),
]
