import asyncio
import threading

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.models import JobStatus
from app.persistence.repository import AnalysisJobRepository


def test_simultaneous_claims(pipeline_case, session_factory, monkeypatch):
    case = pipeline_case
    barrier = threading.Barrier(2)
    original = AnalysisJobRepository.claim_pending

    def claim(self, job_id):
        barrier.wait(timeout=5)
        original(self, job_id)

    monkeypatch.setattr(AnalysisJobRepository, "claim_pending", claim)

    async def run():
        await asyncio.gather(
            case.pipeline.process(case.job_id), case.pipeline.process(case.job_id)
        )

    asyncio.run(run())
    case.transcriber.transcribe.assert_awaited_once()
    case.analyzer.analyze.assert_awaited_once()
    with session_factory() as session:
        assert (
            AnalysisJobRepository(session).get_by_id(case.job_id).status
            == JobStatus.COMPLETED
        )


@pytest.mark.parametrize("stage", ["transcription", "analysis"])
def test_second_execution_skips_inflight_job(pipeline_case, stage):
    case = pipeline_case

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        provider = (
            case.transcriber.transcribe
            if stage == "transcription"
            else case.analyzer.analyze
        )
        result = provider.return_value

        async def blocked(*args):
            entered.set()
            await release.wait()
            return result

        provider.side_effect = blocked
        first = asyncio.create_task(case.pipeline.process(case.job_id))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.wait_for(case.pipeline.process(case.job_id), timeout=5)
            provider.assert_awaited_once()
        finally:
            release.set()
            await first

    asyncio.run(run())
    case.transcriber.transcribe.assert_awaited_once()
    case.analyzer.analyze.assert_awaited_once()


@pytest.mark.parametrize("failed", [False, True])
def test_terminal_reexecution(pipeline_case, session_factory, failed):
    case = pipeline_case
    if failed:
        case.analyzer.analyze.side_effect = RuntimeError("failed")

    async def run():
        if failed:
            with pytest.raises(RuntimeError):
                await case.pipeline.process(case.job_id)
        else:
            await case.pipeline.process(case.job_id)
        with session_factory() as session:
            job = AnalysisJobRepository(session).get_by_id(case.job_id)
            snapshot = (job.status, job.analysis, job.transcript, job.updated_at)
        await case.pipeline.process(case.job_id)
        with session_factory() as session:
            job = AnalysisJobRepository(session).get_by_id(case.job_id)
            assert (
                job.status,
                job.analysis,
                job.transcript,
                job.updated_at,
            ) == snapshot

    asyncio.run(run())
    case.transcriber.transcribe.assert_awaited_once()
    case.analyzer.analyze.assert_awaited_once()


def test_blocking_persistence_leaves_loop_responsive(pipeline_case, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = AnalysisJobRepository.claim_pending

    def claim(self, job_id):
        entered.set()
        assert release.wait(timeout=5)
        original(self, job_id)

    monkeypatch.setattr(AnalysisJobRepository, "claim_pending", claim)

    async def run():
        task = asyncio.create_task(pipeline_case.pipeline.process(pipeline_case.job_id))
        try:
            # This wait and the following loop tick must run while DB is blocked.
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.wait_for(asyncio.sleep(0), timeout=1)
            assert not task.done()
        finally:
            release.set()
            await task

    asyncio.run(run())


def test_each_session_stays_in_its_worker(pipeline_case, session_factory):
    loop_thread = threading.get_ident()
    sessions = []

    class ThreadBoundSession(Session):
        def __init__(self, *args, **kwargs):
            self.owner = threading.get_ident()
            assert self.owner != loop_thread
            super().__init__(*args, **kwargs)
            sessions.append(self)

        def execute(self, *args, **kwargs):
            assert threading.get_ident() == self.owner
            return super().execute(*args, **kwargs)

        def scalar(self, *args, **kwargs):
            assert threading.get_ident() == self.owner
            return super().scalar(*args, **kwargs)

        def close(self):
            assert threading.get_ident() == self.owner
            self.was_closed = True
            super().close()

    pipeline_case.pipeline.session_factory = sessionmaker(
        bind=session_factory.kw["bind"], class_=ThreadBoundSession
    )
    asyncio.run(pipeline_case.pipeline.process(pipeline_case.job_id))
    assert len(sessions) == 3
    assert all(
        session.was_closed and not session.in_transaction() for session in sessions
    )
