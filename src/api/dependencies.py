from typing import Annotated, cast

from fastapi import Depends, Request

from src.config import Settings
from src.models import GitFilters, GroupingFilters, TimeWindow
from src.models.auth import CurrentUser
from src.repositories import (
    E2ERepository,
    ProductUpdateRepository,
    SuggestionRepository,
    UnitRepository,
)
from src.repositories.authorization import AuthorizationRepository
from src.security.auth import get_authenticated_user, get_optional_user
from src.services.authorization import AuthorizationService
from src.services.suggestion_images import SuggestionImageStore


def get_settings_from_app(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_unit_repository(request: Request) -> UnitRepository:
    return UnitRepository(request.app.state.database, request.app.state.settings)


def get_e2e_repository(request: Request) -> E2ERepository:
    return E2ERepository(request.app.state.database, request.app.state.settings)


def get_suggestion_repository(request: Request) -> SuggestionRepository:
    return SuggestionRepository(request.app.state.database, request.app.state.settings)


def get_authorization_repository(request: Request) -> AuthorizationRepository:
    return AuthorizationRepository(request.app.state.database, request.app.state.settings)


def get_authorization_service(
    repository: Annotated[
        AuthorizationRepository,
        Depends(get_authorization_repository),
    ],
) -> AuthorizationService:
    return AuthorizationService(repository)


def get_suggestion_image_store(request: Request) -> SuggestionImageStore:
    return cast(SuggestionImageStore, request.app.state.suggestion_image_store)


def get_product_update_repository(request: Request) -> ProductUpdateRepository:
    return ProductUpdateRepository(request.app.state.database, request.app.state.settings)


def get_product_update_image_store(request: Request) -> SuggestionImageStore:
    return cast(SuggestionImageStore, request.app.state.product_update_image_store)


GitFilterDependency = Annotated[GitFilters, Depends(GitFilters)]
TimeWindowDependency = Annotated[TimeWindow, Depends(TimeWindow)]
GroupingFilterDependency = Annotated[GroupingFilters, Depends(GroupingFilters)]
CurrentUserDependency = Annotated[CurrentUser, Depends(get_authenticated_user)]
OptionalUserDependency = Annotated[CurrentUser, Depends(get_optional_user)]
AuthorizationRepositoryDependency = Annotated[
    AuthorizationRepository,
    Depends(get_authorization_repository),
]
AuthorizationServiceDependency = Annotated[
    AuthorizationService,
    Depends(get_authorization_service),
]
SettingsDependency = Annotated[Settings, Depends(get_settings_from_app)]
UnitRepositoryDependency = Annotated[UnitRepository, Depends(get_unit_repository)]
E2ERepositoryDependency = Annotated[E2ERepository, Depends(get_e2e_repository)]
SuggestionRepositoryDependency = Annotated[
    SuggestionRepository,
    Depends(get_suggestion_repository),
]
SuggestionImageStoreDependency = Annotated[
    SuggestionImageStore,
    Depends(get_suggestion_image_store),
]
ProductUpdateRepositoryDependency = Annotated[
    ProductUpdateRepository,
    Depends(get_product_update_repository),
]
ProductUpdateImageStoreDependency = Annotated[
    SuggestionImageStore,
    Depends(get_product_update_image_store),
]
