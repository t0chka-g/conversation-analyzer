from io import BytesIO
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.application.create_job import create_job
from app.persistence.database import create_database_engine, create_session_factory
from app.persistence.models import AnalysisJob, JobStatus
from app.persistence.repository import AnalysisJobRepository
from app.storage.local import AudioStorageError


def test_create_job(session_factory, storage):
    job_id = create_job(
        BytesIO(b"audio"),
        filename="recording.wav",
        source="upload",
        storage=storage,
        session_factory=session_factory,
    )
    assert isinstance(job_id, UUID)
    with session_factory() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.status == JobStatus.PENDING
        assert job.source == "upload"
        assert storage.get_path(job.audio_path).read_bytes() == b"audio"
        assert job.transcript is job.analysis is None


@pytest.mark.parametrize(
    ("content", "filename"),
    [(b"", "a.wav"), (b"a", "a.exe"), (b"a" * (1024 * 1024 + 1), "a.mp3")],
)
def test_invalid_audio_creates_no_job(session_factory, storage, content, filename):
    with pytest.raises(AudioStorageError):
        create_job(
            BytesIO(content),
            filename=filename,
            source="upload",
            storage=storage,
            session_factory=session_factory,
        )
    with session_factory() as session:
        assert session.scalar(select(AnalysisJob)) is None
    assert not storage.directory.exists() or not list(storage.directory.iterdir())


def test_invalid_source_does_not_store_audio(session_factory, storage):
    with pytest.raises(ValueError):
        create_job(
            BytesIO(b"audio"),
            filename="audio.wav",
            source=" ",
            storage=storage,
            session_factory=session_factory,
        )
    assert not storage.directory.exists()


def test_db_failure_compensates_audio(storage, tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'uninitialized.db'}")
    try:
        with pytest.raises(OperationalError):
            create_job(
                BytesIO(b"audio"),
                filename="audio.wav",
                source="upload",
                storage=storage,
                session_factory=create_session_factory(engine),
            )
        assert not list(storage.directory.iterdir())
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


def test_commit_failure_rolls_back_and_compensates(session_factory, storage):
    class FailingCommitSession(Session):
        pass

    factory = sessionmaker(bind=session_factory.kw["bind"], class_=FailingCommitSession)

    @event.listens_for(FailingCommitSession, "before_commit")
    def fail_commit(session):
        raise RuntimeError("commit failed")

    with pytest.raises(RuntimeError, match="commit failed"):
        create_job(
            BytesIO(b"audio"),
            filename="audio.wav",
            source="upload",
            storage=storage,
            session_factory=factory,
        )
    with session_factory() as session:
        assert session.scalar(select(AnalysisJob)) is None
    assert not list(storage.directory.iterdir())


def test_storage_failure_leaves_database_empty(session_factory, storage, monkeypatch):
    def fail_write(*args, **kwargs):
        raise AudioStorageError("write failed")

    monkeypatch.setattr(storage, "save", fail_write)
    with pytest.raises(AudioStorageError):
        create_job(
            BytesIO(b"audio"),
            filename="audio.wav",
            source="upload",
            storage=storage,
            session_factory=session_factory,
        )
    with session_factory() as session:
        assert session.scalar(select(AnalysisJob)) is None


def test_compensation_failure_preserves_original_error(
    storage, tmp_path, monkeypatch, caplog
):
    def fail_delete(key):
        raise AudioStorageError("sensitive details must not be logged")

    monkeypatch.setattr(storage, "delete", fail_delete)
    engine = create_database_engine(f"sqlite:///{tmp_path / 'uninitialized.db'}")
    try:
        with pytest.raises(OperationalError):
            create_job(
                BytesIO(b"audio"),
                filename="audio.wav",
                source="upload",
                storage=storage,
                session_factory=create_session_factory(engine),
            )
        assert "orphan possible" in caplog.text
        assert "sensitive details" not in caplog.text
        assert len(list(storage.directory.iterdir())) == 1
    finally:
        engine.dispose()
