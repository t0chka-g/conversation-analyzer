from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from app.application.runner import ProcessingRuntime
from app.storage.local import LocalAudioStorage


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.analysis_sessions


def get_audio_storage(request: Request) -> LocalAudioStorage:
    return request.app.state.audio_storage


def get_processing_runtime(request: Request) -> ProcessingRuntime:
    return request.app.state.processing_runtime
