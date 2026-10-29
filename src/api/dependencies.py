from typing import Annotated, cast

from fastapi import Depends, Request

from src.config import Settings
from src.models import GitFilters, GroupingFilters, TimeWindow
from src.repositories import E2ERepository, ProductUpdateRepository, UnitRepository
from src.services.suggestion_images import SuggestionImageStore


def get_settings_from_app(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_unit_repository(request: Request) -> UnitRepository:
    return UnitRepository(request.app.state.database, request.app.state.settings)


def get_e2e_repository(request: Request) -> E2ERepository:
    return E2ERepository(request.app.state.database, request.app.state.settings)


def get_product_update_repository(request: Request) -> ProductUpdateRepository:
    return ProductUpdateRepository(request.app.state.database, request.app.state.settings)


def get_product_update_image_store(request: Request) -> SuggestionImageStore:
    return cast(SuggestionImageStore, request.app.state.product_update_image_store)


GitFilterDependency = Annotated[GitFilters, Depends(GitFilters)]
GroupingFilterDependency = Annotated[GroupingFilters, Depends(GroupingFilters)]
TimeWindowDependency = Annotated[TimeWindow, Depends(TimeWindow)]
SettingsDependency = Annotated[Settings, Depends(get_settings_from_app)]
UnitRepositoryDependency = Annotated[UnitRepository, Depends(get_unit_repository)]
E2ERepositoryDependency = Annotated[E2ERepository, Depends(get_e2e_repository)]
ProductUpdateRepositoryDependency = Annotated[
    ProductUpdateRepository, Depends(get_product_update_repository)
]
ProductUpdateImageStoreDependency = Annotated[
    SuggestionImageStore, Depends(get_product_update_image_store)
]
