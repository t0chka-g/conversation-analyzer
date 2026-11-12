from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.analysis.models import AnalysisResult
from app.persistence.models import JobStatus


class AnalysisAccepted(BaseModel):
    job_id: UUID
    status: Literal[JobStatus.PENDING] = JobStatus.PENDING


class JobFailure(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")
    stage: Literal["transcription", "analysis", "stt", "llm"]


class AnalysisResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    transcript: str | None
    analysis: AnalysisResult | None
    error: JobFailure | None
