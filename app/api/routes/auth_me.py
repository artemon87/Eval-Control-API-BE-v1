from fastapi import APIRouter
from src.api.dependencies import AuthorizationServiceDependency, CurrentUserDependency

from src.models.authorization import AuthorizationContext
from src.security.authorization_identity import identity_from_authenticated_user

router = APIRouter(prefix="/auth", tags=["authorization"])


@router.get("/me", response_model=AuthorizationContext)
async def get_my_authorization(
    current_user: CurrentUserDependency,
    service: AuthorizationServiceDependency,
) -> AuthorizationContext:
    identity = identity_from_authenticated_user(current_user)
    return await service.resolve(identity)
