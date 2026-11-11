import asyncio
from uuid import uuid4

import pytest

from app.persistence.models import JobStatus
from app.persistence.repository import AnalysisJobNotFoundError, AnalysisJobRepository


def test_happy_path(pipeline_case, session_factory, analysis_result):
    case = pipeline_case
    engine = session_factory.kw["bind"]

    async def transcribe(path):
        assert engine.pool.checkedout() == 0
        assert path.read_bytes() == b"test audio"
        with session_factory() as session:
            assert AnalysisJobRepository(session).get_by_id(case.job_id).status == (
                JobStatus.TRANSCRIBING
            )
        return "  Какая цель?\n"

    async def analyze(transcript):
        assert engine.pool.checkedout() == 0
        assert transcript == "  Какая цель?\n"
        with session_factory() as session:
            job = AnalysisJobRepository(session).get_by_id(case.job_id)
            assert job.status == JobStatus.ANALYZING
            assert job.transcript == transcript
        return analysis_result

    case.transcriber.transcribe.side_effect = transcribe
    case.analyzer.analyze.side_effect = analyze
    asyncio.run(case.pipeline.process(case.job_id))
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert job.status == JobStatus.COMPLETED
        assert job.transcript == "  Какая цель?\n"
        assert job.analysis == analysis_result.model_dump(mode="json")
        assert job.error_code is None and job.error_stage is None
    case.transcriber.transcribe.assert_awaited_once()
    case.analyzer.analyze.assert_awaited_once_with("  Какая цель?\n")


def test_missing_job(pipeline_case):
    with pytest.raises(AnalysisJobNotFoundError):
        asyncio.run(pipeline_case.pipeline.process(uuid4()))
    pipeline_case.transcriber.transcribe.assert_not_awaited()
    pipeline_case.analyzer.analyze.assert_not_awaited()


@pytest.mark.parametrize("stage", ["transcription", "analysis"])
def test_cancellation_propagates(pipeline_case, session_factory, stage):
    case = pipeline_case
    provider = (
        case.transcriber.transcribe
        if stage == "transcription"
        else case.analyzer.analyze
    )
    provider.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(case.pipeline.process(case.job_id))
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(case.job_id)
        assert job.status == (
            JobStatus.TRANSCRIBING if stage == "transcription" else JobStatus.ANALYZING
        )
        assert job.error_code is None
