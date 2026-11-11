from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.application.runner import ProcessingRuntime
from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with ProcessingRuntime(settings) as runtime:
            application.state.processing_runtime = runtime
            yield

    application = FastAPI(
        title=settings.app_name, debug=settings.debug, lifespan=lifespan
    )
    application.include_router(health_router)
    return application


app = create_app()
