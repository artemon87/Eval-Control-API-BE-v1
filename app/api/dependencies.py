from typing import Annotated, cast

from fastapi import Depends, Request

from app.config import Settings
from app.repositories import E2ERepository, ProductUpdateRepository, UnitRepository
from app.repositories.base import GitFilters
from app.services.suggestion_images import SuggestionImageStore


def get_settings_from_app(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_git_filters(
    github_repository: str | None = None,
    git_ref: str | None = None,
    git_sha: str | None = None,
) -> GitFilters:
    return GitFilters(
        github_repository=github_repository,
        git_ref=git_ref,
        git_sha=git_sha,
    )


def get_unit_repository(request: Request) -> UnitRepository:
    return UnitRepository(request.app.state.database, request.app.state.settings)


def get_e2e_repository(request: Request) -> E2ERepository:
    return E2ERepository(request.app.state.database, request.app.state.settings)


def get_product_update_repository(request: Request) -> ProductUpdateRepository:
    return ProductUpdateRepository(request.app.state.database, request.app.state.settings)


def get_product_update_image_store(request: Request) -> SuggestionImageStore:
    return cast(SuggestionImageStore, request.app.state.product_update_image_store)


SettingsDependency = Annotated[Settings, Depends(get_settings_from_app)]
GitFiltersDependency = Annotated[GitFilters, Depends(get_git_filters)]
UnitRepositoryDependency = Annotated[UnitRepository, Depends(get_unit_repository)]
E2ERepositoryDependency = Annotated[E2ERepository, Depends(get_e2e_repository)]
ProductUpdateRepositoryDependency = Annotated[
    ProductUpdateRepository, Depends(get_product_update_repository)
]
ProductUpdateImageStoreDependency = Annotated[
    SuggestionImageStore, Depends(get_product_update_image_store)
]
