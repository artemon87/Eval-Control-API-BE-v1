import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.api.exception_handlers import product_update_not_found_handler
from src.middleware import AuthenticationPlaceholderMiddleware, RequestContextMiddleware
from src.repositories.product_updates import ProductUpdateNotFoundError

from src.api.routes import e2e, evaluation_insights, health, product_updates, suggestions, unit
from src.config import Settings, get_settings
from src.database import database_lifespan
from src.services.suggestion_images import (
    SuggestionImageSettings,
    SuggestionImageStore,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with database_lifespan(app, active_settings):
            app.state.suggestion_image_store = SuggestionImageStore(
                SuggestionImageSettings(
                    collection_name=active_settings.image_attachments_collection,
                    namespace="suggestions",
                    max_files=active_settings.suggestion_image_max_files,
                    max_bytes=active_settings.suggestion_image_max_bytes,
                ),
                database=app.state.database,
            )
            app.state.product_update_image_store = SuggestionImageStore(
                SuggestionImageSettings(
                    collection_name=active_settings.image_attachments_collection,
                    namespace="product-updates",
                    max_files=1,
                    max_bytes=active_settings.product_update_image_max_bytes,
                ),
                database=app.state.database,
            )
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
    app.add_exception_handler(
        ProductUpdateNotFoundError,
        product_update_not_found_handler,
    )
    app.add_middleware(AuthenticationPlaceholderMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Server-Timing"],
    )
    app.include_router(health.router)
    app.include_router(unit.router, prefix=active_settings.api_prefix)
    app.include_router(e2e.router, prefix=active_settings.api_prefix)
    app.include_router(evaluation_insights.router, prefix=active_settings.api_prefix)
    app.include_router(suggestions.router, prefix=active_settings.api_prefix)
    app.include_router(product_updates.router, prefix=active_settings.api_prefix)
    return app


app = create_app()
