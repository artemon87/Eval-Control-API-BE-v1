import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import e2e, health, unit
from app.config import Settings, get_settings
from app.database import database_lifespan
from app.middleware import AuthenticationPlaceholderMiddleware, RequestContextMiddleware
from src.services.suggestion_images import (
    SuggestionImageSettings,
    SuggestionImageStore,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        app.state.suggestion_image_store = SuggestionImageStore(
            SuggestionImageSettings(
                bucket_name=active_settings.gcs_suggestion_bucket,
                object_prefix=active_settings.gcs_suggestion_prefix.strip("/"),
                max_files=active_settings.suggestion_image_max_files,
                max_bytes=active_settings.suggestion_image_max_bytes,
            )
        )

        async with database_lifespan(app, active_settings):
            yield

    logging.basicConfig(
        level=active_settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = FastAPI(
        title=active_settings.app_name,
        version="0.1.0",
        docs_url="/docs" if active_settings.app_env != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = active_settings
    app.add_middleware(AuthenticationPlaceholderMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Server-Timing"],
    )
    app.include_router(health.router)
    app.include_router(unit.router, prefix=active_settings.api_prefix)
    app.include_router(e2e.router, prefix=active_settings.api_prefix)
    return app


app = create_app()
