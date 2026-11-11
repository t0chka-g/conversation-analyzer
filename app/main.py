from contextlib import AsyncExitStack, asynccontextmanager

from anyio.to_thread import run_sync
from fastapi import FastAPI

from app.api.analyses import router as analyses_router
from app.api.health import router as health_router
from app.application.runner import ProcessingRuntime
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.persistence.database import create_database_engine, create_session_factory
from app.storage.local import LocalAudioStorage


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        async with AsyncExitStack() as resources:
            engine = await run_sync(create_database_engine, settings.database_url)
            resources.push_async_callback(run_sync, engine.dispose)
            storage = await run_sync(
                LocalAudioStorage,
                settings.audio_storage_dir,
                settings.max_audio_size_mb,
            )
            sessions = create_session_factory(engine)
            runtime = await resources.enter_async_context(
                ProcessingRuntime(settings, session_factory=sessions, storage=storage)
            )
            application.state.analysis_sessions = sessions
            application.state.audio_storage = storage
            application.state.processing_runtime = runtime
            yield

    application = FastAPI(
        title=settings.app_name, debug=settings.debug, lifespan=lifespan
    )
    application.include_router(health_router)
    application.include_router(analyses_router)
    return application


app = create_app()
