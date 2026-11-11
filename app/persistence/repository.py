import re
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.analysis.models import AnalysisResult
from app.persistence.models import AnalysisJob, JobStatus, utc_now


class AnalysisJobNotFoundError(Exception):
    """No job exists for the supplied UUID."""


class InvalidJobTransitionError(Exception):
    """A conditional transition did not match the current job state."""


def validate_source(source: str) -> str:
    if not isinstance(source, str) or not source.strip() or len(source) > 100:
        raise ValueError("source must be nonempty text of at most 100 characters")
    return source.strip()


class AnalysisJobRepository:
    """Session-scoped operations. The caller owns commit, rollback and close."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, *, job_id: UUID, source: str, audio_path: str) -> AnalysisJob:
        source = validate_source(source)
        path = PurePosixPath(audio_path)
        if (
            not audio_path
            or not path.parts
            or path.is_absolute()
            or path.as_posix() != audio_path
            or any(part in {".", ".."} for part in path.parts)
            or "\\" in audio_path
            or ":" in audio_path
            or "\x00" in audio_path
        ):
            raise ValueError("audio_path must be a relative storage key")
        job = AnalysisJob(
            id=str(UUID(str(job_id))),
            source=source,
            audio_path=audio_path,
            status=JobStatus.PENDING,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get_by_id(self, job_id: UUID) -> AnalysisJob:
        job = self.session.scalar(
            select(AnalysisJob)
            .where(AnalysisJob.id == str(UUID(str(job_id))))
            .execution_options(populate_existing=True)
        )
        if job is None:
            raise AnalysisJobNotFoundError(str(job_id))
        return job

    def claim_pending(self, job_id: UUID) -> None:
        self._transition(job_id, JobStatus.PENDING, JobStatus.TRANSCRIBING)

    def save_transcript(self, job_id: UUID, transcript: str) -> None:
        if not isinstance(transcript, str) or not transcript.strip():
            raise ValueError("transcript must be nonempty text")
        self._transition(
            job_id, JobStatus.TRANSCRIBING, JobStatus.ANALYZING, transcript=transcript
        )

    def complete(self, job_id: UUID, result: AnalysisResult) -> None:
        if not isinstance(result, AnalysisResult):
            raise TypeError("result must be a validated AnalysisResult")
        # Revalidate even mutated/model_construct() instances before persisting.
        validated = AnalysisResult.model_validate(result.model_dump(mode="json"))
        self._transition(
            job_id,
            JobStatus.ANALYZING,
            JobStatus.COMPLETED,
            analysis=validated.model_dump(mode="json"),
            require_transcript=True,
        )

    def fail(
        self,
        job_id: UUID,
        *,
        error_code: str,
        error_stage: Literal["stt", "llm"],
    ) -> None:
        if not isinstance(error_code, str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,63}", error_code
        ):
            raise ValueError("error_code must be an uppercase machine-readable code")
        stages = {"stt": JobStatus.TRANSCRIBING, "llm": JobStatus.ANALYZING}
        if error_stage not in stages:
            raise ValueError("error_stage must be stt or llm")
        self._transition(
            job_id,
            stages[error_stage],
            JobStatus.FAILED,
            error_code=error_code,
            error_stage=error_stage,
        )

    def _transition(
        self,
        job_id: UUID,
        expected: JobStatus,
        target: JobStatus,
        *,
        require_transcript: bool = False,
        **values,
    ) -> None:
        statement = update(AnalysisJob).where(
            AnalysisJob.id == str(UUID(str(job_id))), AnalysisJob.status == expected
        )
        if require_transcript:
            statement = statement.where(
                AnalysisJob.transcript.is_not(None),
                func.length(func.trim(AnalysisJob.transcript, " \t\n\r\v\f")) > 0,
            )
        changed = self.session.execute(
            statement.values(status=target, updated_at=utc_now(), **values),
            execution_options={"synchronize_session": "fetch"},
        )
        if changed.rowcount != 1:
            self.get_by_id(job_id)
            raise InvalidJobTransitionError(
                f"Job {job_id} cannot transition from {expected} to {target}"
            )
