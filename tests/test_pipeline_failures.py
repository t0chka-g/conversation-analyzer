import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from app.analysis.errors import AnalysisProviderError, AnalysisValidationError
from app.analysis.grounding import validate_quotes
from app.application.pipeline import EmptyTranscriptError
from app.persistence.models import JobStatus
from app.persistence.repository import AnalysisJobRepository
from app.transcription.base import TranscriptionProviderError


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (TranscriptionProviderError("secret conversation"), "TRANSCRIPTION_FAILED"),
        (TimeoutError("secret"), "TRANSCRIPTION_TIMEOUT"),
        ("", "EMPTY_TRANSCRIPT"),
        (" \t\n", "EMPTY_TRANSCRIPT"),
        (None, "EMPTY_TRANSCRIPT"),
    ],
)
def test_stt_failures(pipeline_case, session_factory, caplog, failure, code):
    case = pipeline_case
    if isinstance(failure, Exception):
        case.transcriber.transcribe.side_effect = failure
        expected = type(failure)
    else:
        case.transcriber.transcribe.return_value = failure
        expected = EmptyTranscriptError
    with pytest.raises(expected):
        asyncio.run(case.pipeline.process(case.job_id))
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert (job.status, job.error_stage, job.error_code) == (
            JobStatus.FAILED,
            "transcription",
            code,
        )
        assert job.transcript is None and job.analysis is None
    case.analyzer.analyze.assert_not_awaited()
    assert "secret" not in caplog.text


def test_missing_audio(pipeline_case, session_factory):
    case = pipeline_case
    with session_factory() as session:
        key = AnalysisJobRepository(session).get_by_id(case.job_id).audio_path
    case.pipeline.storage.delete(key)
    with pytest.raises(FileNotFoundError):
        asyncio.run(case.pipeline.process(case.job_id))
    case.transcriber.transcribe.assert_not_awaited()
    case.analyzer.analyze.assert_not_awaited()
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert job.status == JobStatus.FAILED
        assert job.error_code == "AUDIO_UNAVAILABLE"


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (AnalysisProviderError("secret response"), "ANALYSIS_FAILED"),
        (TimeoutError("secret"), "ANALYSIS_TIMEOUT"),
        (AnalysisValidationError("bad structured output"), "ANALYSIS_INVALID"),
        (RuntimeError("programming error"), "ANALYSIS_FAILED"),
    ],
)
def test_llm_failures(pipeline_case, session_factory, caplog, failure, code):
    case = pipeline_case
    case.analyzer.analyze.side_effect = failure
    with pytest.raises(type(failure)) as raised:
        asyncio.run(case.pipeline.process(case.job_id))
    assert raised.value is failure
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert (job.status, job.error_stage, job.error_code) == (
            JobStatus.FAILED,
            "analysis",
            code,
        )
        assert job.transcript == "  Какая цель?\n" and job.analysis is None
    case.transcriber.transcribe.assert_awaited_once()
    assert "secret" not in caplog.text


def test_grounding_failure(pipeline_case, session_factory, analysis_result):
    case = pipeline_case
    analysis_result.strengths[0].quote = "Несуществующая цитата"

    async def analyze(transcript):
        validate_quotes(analysis_result, transcript)
        return analysis_result

    case.analyzer.analyze.side_effect = analyze
    with pytest.raises(AnalysisValidationError):
        asyncio.run(case.pipeline.process(case.job_id))
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert job.status == JobStatus.FAILED and job.analysis is None
        assert job.transcript == "  Какая цель?\n"
        assert job.error_code == "ANALYSIS_INVALID"


@pytest.mark.parametrize("operation", ["save_transcript", "complete"])
@pytest.mark.parametrize("when", ["write", "commit"])
def test_persistence_rollback(
    pipeline_case, session_factory, monkeypatch, operation, when
):
    case = pipeline_case
    original = getattr(AnalysisJobRepository, operation)
    error = SQLAlchemyError("secret persistence data")

    def broken(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if when == "write":
            raise error
        self.session.info["fail_commit"] = True

    def before_commit(session):
        if session.info.get("fail_commit"):
            raise error

    monkeypatch.setattr(AnalysisJobRepository, operation, broken)
    event.listen(session_factory, "before_commit", before_commit)
    try:
        with pytest.raises(SQLAlchemyError) as raised:
            asyncio.run(case.pipeline.process(case.job_id))
        assert raised.value is error
    finally:
        event.remove(session_factory, "before_commit", before_commit)
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert job.status == JobStatus.FAILED and job.analysis is None
        assert job.transcript == (
            None if operation == "save_transcript" else "  Какая цель?\n"
        )
        assert job.error_stage == (
            "transcription" if operation == "save_transcript" else "analysis"
        )
        assert job.error_code == (
            "TRANSCRIPT_PERSISTENCE_FAILED"
            if operation == "save_transcript"
            else "RESULT_PERSISTENCE_FAILED"
        )
    if operation == "save_transcript":
        case.analyzer.analyze.assert_not_awaited()
    assert session_factory.kw["bind"].pool.checkedout() == 0


def test_failure_write_unavailable_preserves_original(
    pipeline_case, monkeypatch, caplog
):
    original = AnalysisProviderError("secret original")
    pipeline_case.analyzer.analyze.side_effect = original

    def broken(*args, **kwargs):
        raise SQLAlchemyError("secret DB error")

    monkeypatch.setattr(AnalysisJobRepository, "fail", broken)
    with pytest.raises(AnalysisProviderError) as raised:
        asyncio.run(pipeline_case.pipeline.process(pipeline_case.job_id))
    assert raised.value is original
    assert "Failure persistence unavailable" in caplog.text
    assert "secret" not in caplog.text


def test_late_error_does_not_change_completed_job(
    pipeline_case, session_factory, analysis_result
):
    case = pipeline_case

    async def analyze(transcript):
        with session_factory.begin() as session:
            AnalysisJobRepository(session).complete(case.job_id, analysis_result)
        raise AnalysisProviderError("late error")

    case.analyzer.analyze.side_effect = analyze
    with pytest.raises(AnalysisProviderError):
        asyncio.run(case.pipeline.process(case.job_id))
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.error_code is None and job.analysis is not None


def test_wrapped_timeout_keeps_chain(pipeline_case, session_factory):
    root = TimeoutError("secret")
    wrapped = TranscriptionProviderError("provider failed")
    wrapped.__cause__ = root
    pipeline_case.transcriber.transcribe.side_effect = wrapped
    with pytest.raises(TranscriptionProviderError) as raised:
        asyncio.run(pipeline_case.pipeline.process(pipeline_case.job_id))
    assert raised.value.__cause__ is root
    with session_factory() as session:
        assert (
            AnalysisJobRepository(session).get_by_id(pipeline_case.job_id).error_code
            == "TRANSCRIPTION_TIMEOUT"
        )


@pytest.mark.parametrize("mutated", [False, True])
def test_invalid_result_cannot_complete(
    pipeline_case, session_factory, analysis_result, mutated
):
    from pydantic import ValidationError

    if mutated:
        analysis_result.summary = " "
        result = analysis_result
    else:
        result = analysis_result.model_dump()
    pipeline_case.analyzer.analyze.return_value = result
    with pytest.raises((TypeError, ValidationError)):
        asyncio.run(pipeline_case.pipeline.process(pipeline_case.job_id))
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(pipeline_case.job_id)
        assert job.status == JobStatus.FAILED and job.analysis is None
        assert job.transcript == "  Какая цель?\n"


def test_claim_commit_failure_rolls_back_before_provider_calls(
    pipeline_case, session_factory
):
    def fail_commit(session):
        raise SQLAlchemyError("secret")

    event.listen(session_factory, "before_commit", fail_commit)
    try:
        with pytest.raises(SQLAlchemyError):
            asyncio.run(pipeline_case.pipeline.process(pipeline_case.job_id))
    finally:
        event.remove(session_factory, "before_commit", fail_commit)
    pipeline_case.transcriber.transcribe.assert_not_awaited()
    pipeline_case.analyzer.analyze.assert_not_awaited()
    with session_factory() as session:
        assert (
            AnalysisJobRepository(session).get_by_id(pipeline_case.job_id).status
            == JobStatus.PENDING
        )
