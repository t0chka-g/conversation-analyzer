import asyncio
import logging
from contextlib import AsyncExitStack
from uuid import UUID

from anyio.to_thread import run_sync
from sqlalchemy.orm import Session, sessionmaker

from app.application.pipeline import ProcessingPipeline
from app.core.config import Settings
from app.persistence.database import create_database_engine, create_session_factory
from app.storage.local import LocalAudioStorage

logger = logging.getLogger(__name__)


def _create_analyzer(settings: Settings):
    from app.analysis.openai import create_analyzer

    return create_analyzer(settings)


def _create_transcriber(settings: Settings):
    from app.transcription.factory import create_transcriber

    return create_transcriber(settings)


class ProcessingRuntime:
    """Application/CLI-owned resources, initialized on first processing request."""

    def __init__(
        self,
        settings: Settings,
        *,
        session_factory: sessionmaker[Session] | None = None,
        storage: LocalAudioStorage | None = None,
    ) -> None:
        if (session_factory is None) != (storage is None):
            raise ValueError("Provide both session_factory and storage, or neither")
        self.settings = settings
        self._session_factory = session_factory
        self._storage = storage
        self._pipeline: ProcessingPipeline | None = None
        self._lock = asyncio.Lock()
        self._resources = AsyncExitStack()

    async def get_pipeline(self) -> ProcessingPipeline:
        async with self._lock:
            if self._pipeline is None:
                # Imports and constructors can read prompts, resolve storage paths
                # and import native STT dependencies. Keep them off the loop too.
                async with AsyncExitStack() as resources:
                    sessions = self._session_factory
                    storage = self._storage
                    if sessions is None:
                        engine = await run_sync(
                            create_database_engine, self.settings.database_url
                        )
                        resources.push_async_callback(run_sync, engine.dispose)
                        sessions = create_session_factory(engine)
                        storage = await run_sync(
                            LocalAudioStorage,
                            self.settings.audio_storage_dir,
                            self.settings.max_audio_size_mb,
                        )
                    analyzer = await run_sync(_create_analyzer, self.settings)
                    resources.push_async_callback(analyzer.aclose)
                    transcriber = await run_sync(_create_transcriber, self.settings)
                    if close := getattr(transcriber, "aclose", None):
                        resources.push_async_callback(close)
                    self._pipeline = ProcessingPipeline(
                        transcriber=transcriber,
                        analyzer=analyzer,
                        storage=storage,
                        session_factory=sessions,
                    )
                    self._resources = resources.pop_all()
            return self._pipeline

    async def __aenter__(self) -> "ProcessingRuntime":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._resources.aclose()


async def run_analysis_job(job_id: UUID, *, runtime: ProcessingRuntime) -> None:
    """BackgroundTasks callback: pass an app-owned runtime, never a DB session."""
    try:
        pipeline = await runtime.get_pipeline()
        await pipeline.process(job_id)
    except Exception:
        logger.error("Background processing failed job_id=%s stage=runner", job_id)
        raise


async def run_background_analysis(job_id: UUID, *, runtime: ProcessingRuntime) -> None:
    """HTTP callback: the response is sent; runner already logs safely.

    Pipeline records FAILED where possible. Do not let provider exceptions escape
    into the ASGI server's traceback logger after the response has been sent.
    Cancellation still propagates. CLI callers retain run_analysis_job semantics.
    """
    try:
        await run_analysis_job(job_id, runtime=runtime)
    except Exception:
        logger.error(
            "job_id=%s endpoint=upload_analysis stage=background code=BACKGROUND_FAILED",
            job_id,
        )
