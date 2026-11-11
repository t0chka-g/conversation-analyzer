import logging
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.persistence.repository import AnalysisJobRepository, validate_source
from app.storage.local import AudioStorageError, LocalAudioStorage

logger = logging.getLogger(__name__)


def create_job(
    stream: BinaryIO,
    *,
    filename: str,
    source: str,
    storage: LocalAudioStorage,
    session_factory: sessionmaker[Session],
) -> UUID:
    """Save audio, commit a PENDING job, compensate ordinary database failures."""
    source = validate_source(source)
    job_id = uuid4()
    key = storage.save(stream, filename=filename)
    try:
        with session_factory.begin() as session:
            AnalysisJobRepository(session).create(
                job_id=job_id, source=source, audio_path=key
            )
    except Exception:
        try:
            storage.delete(key)
        except AudioStorageError:
            # No exception detail, transcript, provider payload or local path in logs.
            logger.error(
                "Audio compensation failed for job %s; orphan possible", job_id
            )
        raise
    return job_id
