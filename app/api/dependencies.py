from typing import Annotated, cast

from fastapi import Depends, Request

from app.config import Settings
from app.repositories import E2ERepository, UnitRepository


def get_settings_from_app(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_unit_repository(request: Request) -> UnitRepository:
    return UnitRepository(request.app.state.database, request.app.state.settings)


def get_e2e_repository(request: Request) -> E2ERepository:
    return E2ERepository(request.app.state.database, request.app.state.settings)


SettingsDependency = Annotated[Settings, Depends(get_settings_from_app)]
UnitRepositoryDependency = Annotated[UnitRepository, Depends(get_unit_repository)]
E2ERepositoryDependency = Annotated[E2ERepository, Depends(get_e2e_repository)]
