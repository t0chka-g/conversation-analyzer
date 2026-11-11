from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import update

from app.persistence.models import AnalysisJob, JobStatus
from app.persistence.repository import (
    AnalysisJobNotFoundError,
    AnalysisJobRepository,
    InvalidJobTransitionError,
)


def advance_to(repository, job_id, status, result):
    if status == JobStatus.PENDING:
        return
    repository.claim_pending(job_id)
    if status == JobStatus.TRANSCRIBING:
        return
    repository.save_transcript(job_id, "Какая цель?")
    if status == JobStatus.COMPLETED:
        repository.complete(job_id, result)
    elif status == JobStatus.FAILED:
        repository.fail(job_id, error_code="LLM_FAILURE", error_stage="llm")


def transition(repository, job_id, operation, result):
    if operation == "claim":
        repository.claim_pending(job_id)
    elif operation == "transcript":
        repository.save_transcript(job_id, "Какая цель?")
    elif operation == "complete":
        repository.complete(job_id, result)
    else:
        repository.fail(job_id, error_code="PROVIDER_FAILURE", error_stage=operation)


TRANSITIONS = {
    "claim": (JobStatus.PENDING, JobStatus.TRANSCRIBING),
    "transcript": (JobStatus.TRANSCRIBING, JobStatus.ANALYZING),
    "complete": (JobStatus.ANALYZING, JobStatus.COMPLETED),
    "stt": (JobStatus.TRANSCRIBING, JobStatus.FAILED),
    "llm": (JobStatus.ANALYZING, JobStatus.FAILED),
}


@pytest.mark.parametrize("status", list(JobStatus))
@pytest.mark.parametrize("operation", list(TRANSITIONS))
def test_transition_matrix(session_factory, job_id, analysis_result, status, operation):
    with session_factory.begin() as session:
        advance_to(AnalysisJobRepository(session), job_id, status, analysis_result)
    expected, target = TRANSITIONS[operation]
    if status == expected:
        with session_factory.begin() as session:
            transition(
                AnalysisJobRepository(session), job_id, operation, analysis_result
            )
    else:
        with (
            pytest.raises(InvalidJobTransitionError),
            session_factory.begin() as session,
        ):
            transition(
                AnalysisJobRepository(session), job_id, operation, analysis_result
            )
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.status == (target if status == expected else status)


@pytest.mark.parametrize("stage", ["stt", "llm"])
def test_failure_preserves_intermediate_results(session_factory, job_id, stage):
    with session_factory.begin() as session:
        repository = AnalysisJobRepository(session)
        repository.claim_pending(job_id)
        if stage == "llm":
            repository.save_transcript(job_id, "Какая цель?")
    with session_factory.begin() as session:
        AnalysisJobRepository(session).fail(
            job_id, error_code="PROVIDER_TIMEOUT", error_stage=stage
        )
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.status == JobStatus.FAILED
        assert job.transcript == ("Какая цель?" if stage == "llm" else None)
        assert job.error_code == "PROVIDER_TIMEOUT"
        assert job.error_stage == stage
        assert job.analysis is None


def test_competing_claims(session_factory, job_id):
    barrier = Barrier(2)

    def claim():
        try:
            with session_factory.begin() as session:
                barrier.wait(timeout=5)
                AnalysisJobRepository(session).claim_pending(job_id)
            return True
        except InvalidJobTransitionError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(outcomes) == [False, True]
    with session_factory() as session:
        assert AnalysisJobRepository(session).get_by_id(job_id).status == (
            JobStatus.TRANSCRIBING
        )


def test_competing_completion_and_failure(session_factory, job_id, analysis_result):
    with session_factory.begin() as session:
        advance_to(
            AnalysisJobRepository(session), job_id, JobStatus.ANALYZING, analysis_result
        )
    barrier = Barrier(2)

    def finish(operation):
        try:
            with session_factory.begin() as session:
                barrier.wait(timeout=5)
                transition(
                    AnalysisJobRepository(session), job_id, operation, analysis_result
                )
            return operation
        except InvalidJobTransitionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(finish, ["complete", "llm"]))
    winners = [outcome for outcome in outcomes if outcome]
    assert len(winners) == 1
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.transcript == "Какая цель?"
        assert job.status == TRANSITIONS[winners[0]][1]
        assert (job.analysis is not None) == (job.status == JobStatus.COMPLETED)


def test_stale_session_cannot_claim_again(session_factory, job_id):
    with session_factory() as stale:
        job = AnalysisJobRepository(stale).get_by_id(job_id)
        stale.commit()
        with session_factory.begin() as session:
            AnalysisJobRepository(session).claim_pending(job_id)
        assert job.status == JobStatus.PENDING
        with pytest.raises(InvalidJobTransitionError):
            AnalysisJobRepository(stale).claim_pending(job_id)


@pytest.mark.parametrize("transcript", [None, "", " \n\t"])
def test_invalid_transcript(session_factory, job_id, transcript):
    with session_factory.begin() as session:
        AnalysisJobRepository(session).claim_pending(job_id)
    with pytest.raises(ValueError), session_factory.begin() as session:
        AnalysisJobRepository(session).save_transcript(job_id, transcript)
    with session_factory() as session:
        assert AnalysisJobRepository(session).get_by_id(job_id).status == (
            JobStatus.TRANSCRIBING
        )


@pytest.mark.parametrize("transcript", [None, "", " ", "\t\n"])
def test_cannot_complete_without_transcript(
    session_factory, job_id, analysis_result, transcript
):
    with session_factory.begin() as session:
        # Simulate missing/invalid data imported by a caller outside the repository.
        session.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id == str(job_id))
            .values(status=JobStatus.ANALYZING, transcript=transcript)
        )
    with pytest.raises(InvalidJobTransitionError), session_factory.begin() as session:
        AnalysisJobRepository(session).complete(job_id, analysis_result)


@pytest.mark.parametrize("operation", list(TRANSITIONS))
def test_transitions_on_missing_job(session_factory, analysis_result, operation):
    with pytest.raises(AnalysisJobNotFoundError), session_factory.begin() as session:
        transition(AnalysisJobRepository(session), uuid4(), operation, analysis_result)


def test_cannot_complete_with_unvalidated_json(
    session_factory, job_id, analysis_result
):
    with pytest.raises(TypeError), session_factory.begin() as session:
        AnalysisJobRepository(session).complete(
            job_id, analysis_result.model_dump(mode="json")
        )


def test_mutated_analysis_revalidated(session_factory, job_id, analysis_result):
    with session_factory.begin() as session:
        advance_to(
            AnalysisJobRepository(session), job_id, JobStatus.ANALYZING, analysis_result
        )
    analysis_result.strengths[0].description = " "
    with pytest.raises(ValidationError), session_factory.begin() as session:
        AnalysisJobRepository(session).complete(job_id, analysis_result)
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.analysis is None
        assert job.status == JobStatus.ANALYZING


@pytest.mark.parametrize(
    ("code", "stage"),
    [("exception with secret", "llm"), ("A" * 65, "stt"), ("X", "api")],
)
def test_error_fields_reject_free_text(session_factory, job_id, code, stage):
    with pytest.raises(ValueError), session_factory.begin() as session:
        AnalysisJobRepository(session).fail(job_id, error_code=code, error_stage=stage)
