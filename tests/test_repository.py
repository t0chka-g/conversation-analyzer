from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from app.analysis.models import AnalysisResult
from app.persistence.models import AnalysisJob, JobStatus
from app.persistence.repository import (
    AnalysisJobNotFoundError,
    AnalysisJobRepository,
)


def test_create_and_retrieve(session_factory):
    job_id = uuid4()
    with session_factory.begin() as session:
        job = AnalysisJobRepository(session).create(
            job_id=job_id, source="upload", audio_path=f"{uuid4()}.mp3"
        )
        assert UUID(job.id) == job_id
        assert job.status == JobStatus.PENDING
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.source == "upload"
        assert not job.audio_path.startswith("/")
        assert job.transcript is job.analysis is None
        assert job.error_code is job.error_stage is None
        assert job.created_at.tzinfo is UTC
        assert job.updated_at.tzinfo is UTC
        assert job.created_at <= job.updated_at <= datetime.now(UTC)


def test_missing_job(session_factory):
    with session_factory() as session, pytest.raises(AnalysisJobNotFoundError):
        AnalysisJobRepository(session).get_by_id(uuid4())


def test_results_round_trip(session_factory, job_id, analysis_result):
    with session_factory.begin() as session:
        AnalysisJobRepository(session).claim_pending(job_id)
    with session_factory.begin() as session:
        AnalysisJobRepository(session).save_transcript(job_id, "Какая цель?")
    with session_factory.begin() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        transcript_saved_at = job.updated_at
        assert job.status == JobStatus.ANALYZING
        assert job.analysis is None
    with session_factory.begin() as session:
        AnalysisJobRepository(session).complete(job_id, analysis_result)
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.transcript == "Какая цель?"
        assert job.analysis == analysis_result.model_dump(mode="json")
        assert AnalysisResult.model_validate(job.analysis) == analysis_result
        assert job.created_at <= transcript_saved_at <= job.updated_at


def test_timezone_normalized(session_factory, job_id):
    value = datetime(2026, 1, 1, 15, tzinfo=timezone(timedelta(hours=3)))
    with session_factory.begin() as session:
        AnalysisJobRepository(session).get_by_id(job_id).created_at = value
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.created_at == datetime(2026, 1, 1, 12, tzinfo=UTC)
        assert job.created_at.tzinfo is UTC


def test_naive_timestamp_rejected(session_factory, job_id):
    with pytest.raises(StatementError), session_factory.begin() as session:
        AnalysisJobRepository(session).get_by_id(job_id).updated_at = datetime(
            2026, 1, 1
        )


@pytest.mark.parametrize("source", ["", " \n", "a" * 101, None])
def test_invalid_source(session_factory, source):
    with session_factory.begin() as session, pytest.raises(ValueError):
        AnalysisJobRepository(session).create(
            job_id=uuid4(), source=source, audio_path="audio.wav"
        )
    with session_factory() as session:
        assert session.scalar(select(AnalysisJob)) is None


@pytest.mark.parametrize(
    "path", ["/tmp/a.wav", "../a.wav", "x/../a.wav", "C:\\a.wav", "./a.wav", "", "."]
)
def test_invalid_audio_path(session_factory, path):
    with session_factory.begin() as session, pytest.raises(ValueError):
        AnalysisJobRepository(session).create(
            job_id=uuid4(), source="upload", audio_path=path
        )


def test_rollback_creation(session_factory):
    job_id = uuid4()
    with pytest.raises(RuntimeError), session_factory.begin() as session:
        AnalysisJobRepository(session).create(
            job_id=job_id, source="upload", audio_path="audio.wav"
        )
        raise RuntimeError("abort transaction")
    with session_factory() as session, pytest.raises(AnalysisJobNotFoundError):
        AnalysisJobRepository(session).get_by_id(job_id)


def test_rollback_transcript_and_status_together(session_factory, job_id):
    with session_factory.begin() as session:
        AnalysisJobRepository(session).claim_pending(job_id)
    with pytest.raises(RuntimeError), session_factory.begin() as session:
        AnalysisJobRepository(session).save_transcript(job_id, "Какая цель?")
        raise RuntimeError("abort transaction")
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.status == JobStatus.TRANSCRIBING
        assert job.transcript is None


def test_rollback_completion(session_factory, job_id, analysis_result):
    with session_factory.begin() as session:
        repository = AnalysisJobRepository(session)
        repository.claim_pending(job_id)
        repository.save_transcript(job_id, "Какая цель?")
    with pytest.raises(RuntimeError), session_factory.begin() as session:
        AnalysisJobRepository(session).complete(job_id, analysis_result)
        raise RuntimeError("abort transaction")
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.status == JobStatus.ANALYZING
        assert job.transcript == "Какая цель?"
        assert job.analysis is None
