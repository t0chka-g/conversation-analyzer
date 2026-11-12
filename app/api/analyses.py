import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Response,
    UploadFile,
)
from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas import AnalysisAccepted, AnalysisResponse, JobFailure
from app.application.create_job import create_job
from app.application.runner import ProcessingRuntime, run_background_analysis
from app.core.dependencies import (
    get_audio_storage,
    get_processing_runtime,
    get_session_factory,
)
from app.persistence.models import JobStatus
from app.persistence.repository import AnalysisJobNotFoundError, AnalysisJobRepository
from app.storage.local import (
    AudioTooLargeError,
    InvalidAudioError,
    LocalAudioStorage,
    UnsupportedAudioFormatError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/analyses", tags=["analyses"])
Sessions = Annotated[sessionmaker[Session], Depends(get_session_factory)]
Storage = Annotated[LocalAudioStorage, Depends(get_audio_storage)]
Runtime = Annotated[ProcessingRuntime, Depends(get_processing_runtime)]


@router.post(
    "",
    status_code=202,
    response_model=AnalysisAccepted,
    responses={
        400: {"description": "Invalid or empty audio file"},
        413: {"description": "Audio exceeds configured size limit"},
        415: {"description": "Unsupported audio extension"},
        500: {"description": "Unable to create analysis job"},
    },
)
def upload_analysis(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    response: Response,
    sessions: Sessions,
    storage: Storage,
    runtime: Runtime,
) -> AnalysisAccepted:
    # Sync handlers execute in FastAPI's thread pool. The upload spool is copied
    # in bounded chunks by storage, not read into one in-memory bytes object.
    try:
        with file.file:
            job_id = create_job(
                file.file,
                filename=file.filename or "",
                source="upload",
                storage=storage,
                session_factory=sessions,
            )
    except InvalidAudioError as exc:
        if isinstance(exc, AudioTooLargeError):
            status, code, detail = 413, "AUDIO_TOO_LARGE", "Audio exceeds size limit"
        elif isinstance(exc, UnsupportedAudioFormatError):
            status, code, detail = (
                415,
                "UNSUPPORTED_AUDIO",
                "Unsupported audio extension",
            )
        else:
            status, code, detail = 400, "INVALID_AUDIO", "Invalid or empty audio file"
        logger.info("endpoint=upload_analysis stage=ingestion code=%s", code)
        raise HTTPException(status_code=status, detail=detail) from None
    except Exception:
        logger.error("endpoint=upload_analysis stage=ingestion code=INGESTION_FAILED")
        raise HTTPException(500, "Unable to create analysis job") from None
    background_tasks.add_task(run_background_analysis, job_id, runtime=runtime)
    response.headers["Location"] = f"/api/v1/analyses/{job_id}"
    logger.info(
        "job_id=%s endpoint=upload_analysis stage=accepted code=JOB_ACCEPTED", job_id
    )
    return AnalysisAccepted(job_id=job_id)


@router.get(
    "/{job_id}",
    response_model=AnalysisResponse,
    responses={
        404: {"description": "Analysis job not found"},
        500: {"description": "Unable to read analysis job"},
    },
)
def get_analysis(job_id: UUID, sessions: Sessions) -> AnalysisResponse:
    try:
        with sessions() as session:
            job = AnalysisJobRepository(session).get_by_id(job_id)
            return AnalysisResponse(
                job_id=UUID(job.id),
                status=job.status,
                transcript=job.transcript,
                analysis=job.analysis,
                error=(
                    JobFailure(code=job.error_code, stage=job.error_stage)
                    if job.status == JobStatus.FAILED
                    else None
                ),
            )
    except AnalysisJobNotFoundError:
        raise HTTPException(404, "Analysis job not found") from None
    except Exception:
        logger.error(
            "job_id=%s endpoint=get_analysis stage=read code=JOB_READ_FAILED", job_id
        )
        raise HTTPException(500, "Unable to read analysis job") from None
