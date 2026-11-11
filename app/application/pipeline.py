import logging
from functools import partial
from pathlib import Path
from typing import Literal
from uuid import UUID

from anyio.to_thread import run_sync
from openai import APITimeoutError
from sqlalchemy.orm import Session, sessionmaker

from app.analysis.base import Analyzer
from app.analysis.errors import AnalysisValidationError
from app.persistence.repository import (
    AnalysisJobRepository,
    InvalidJobTransitionError,
)
from app.storage.local import LocalAudioStorage
from app.transcription.base import Transcriber

logger = logging.getLogger(__name__)


class EmptyTranscriptError(ValueError):
    """STT returned no usable text."""


def error_code(exc: Exception, stage: str) -> str:
    """Classify known failures without persisting provider messages."""
    if stage in {"transcript_persistence", "result_persistence"}:
        return stage.upper() + "_FAILED"
    if stage == "audio":
        return "AUDIO_UNAVAILABLE"
    if isinstance(exc, EmptyTranscriptError):
        return "EMPTY_TRANSCRIPT"
    if isinstance(exc, AnalysisValidationError):
        return "ANALYSIS_INVALID"
    cause = exc
    seen = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, (TimeoutError, APITimeoutError)):
            return stage.upper() + "_TIMEOUT"
        cause = cause.__cause__
    return stage.upper() + "_FAILED"


class ProcessingPipeline:
    """Orchestrate providers; each DB operation owns a short worker session.

    Providers and the engine belong to the composition root, not this service.
    Exceptions propagate after a best-effort failure transition.
    """

    def __init__(
        self,
        *,
        transcriber: Transcriber,
        analyzer: Analyzer,
        session_factory: sessionmaker[Session],
        storage: LocalAudioStorage,
    ) -> None:
        self.transcriber = transcriber
        self.analyzer = analyzer
        self.session_factory = session_factory
        self.storage = storage

    def _claim(self, job_id: UUID) -> str | None:
        try:
            with self.session_factory.begin() as session:
                repository = AnalysisJobRepository(session)
                repository.claim_pending(job_id)
                key = repository.get_by_id(job_id).audio_path
            return key
        except InvalidJobTransitionError:
            return None

    def _write(self, operation: str, job_id: UUID, *args, **kwargs) -> None:
        with self.session_factory.begin() as session:
            getattr(AnalysisJobRepository(session), operation)(job_id, *args, **kwargs)

    def _audio_path(self, key: str) -> Path:
        path = self.storage.get_path(key)
        # Check readability before asking a provider to load a model or send data.
        with path.open("rb"):
            pass
        return path

    async def process(self, job_id: UUID) -> None:
        stage = "claim"
        failure_stage: Literal["transcription", "analysis"] = "transcription"
        claimed = False
        try:
            key = await run_sync(self._claim, job_id)
            if key is None:
                logger.info("Job skipped job_id=%s stage=claim", job_id)
                return
            claimed = True
            stage = "audio"
            path = await run_sync(self._audio_path, key)
            stage = "transcription"
            transcript = await self.transcriber.transcribe(path)
            if not isinstance(transcript, str) or not transcript.strip():
                raise EmptyTranscriptError("STT returned an empty transcript")
            stage = "transcript_persistence"
            await run_sync(self._write, "save_transcript", job_id, transcript)
            failure_stage = "analysis"
            stage = "analysis"
            result = await self.analyzer.analyze(transcript)
            stage = "result_persistence"
            await run_sync(self._write, "complete", job_id, result)
            logger.info("Job completed job_id=%s stage=completed", job_id)
        except Exception as exc:
            code = error_code(exc, stage)
            # No exception text/traceback: SDK errors may contain conversation data.
            logger.error("Job failed job_id=%s stage=%s code=%s", job_id, stage, code)
            if claimed:
                try:
                    await run_sync(
                        partial(
                            self._write,
                            "fail",
                            job_id,
                            error_code=code,
                            error_stage=failure_stage,
                        )
                    )
                except InvalidJobTransitionError:
                    logger.warning(
                        "Failure transition skipped job_id=%s stage=%s",
                        job_id,
                        failure_stage,
                    )
                except Exception:
                    logger.error(
                        "Failure persistence unavailable job_id=%s stage=%s",
                        job_id,
                        failure_stage,
                    )
            raise
