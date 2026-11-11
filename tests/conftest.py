import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in (
        "APP_NAME",
        "APP_ENV",
        "DEBUG",
        "LOG_LEVEL",
        "STT_PROVIDER",
        "WHISPER_MODEL",
        "WHISPER_DEVICE",
        "WHISPER_COMPUTE_TYPE",
        "WHISPER_LANGUAGE",
        "WHISPER_CPU_THREADS",
        "OPENAI_API_KEY",
        "OPENAI_STT_MODEL",
        "OPENAI_STT_TIMEOUT",
        "OPENAI_LLM_MODEL",
        "OPENAI_LLM_TIMEOUT",
        "OPENAI_LLM_MAX_RETRIES",
        "DATABASE_URL",
        "AUDIO_STORAGE_DIR",
        "MAX_AUDIO_SIZE_MB",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def session_factory(tmp_path):
    from app.persistence.database import (
        create_database_engine,
        create_session_factory,
        initialize_database,
    )

    engine = create_database_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialize_database(engine)
    yield create_session_factory(engine)
    assert engine.pool.checkedout() == 0
    engine.dispose()


@pytest.fixture
def job_id(session_factory):
    from uuid import uuid4

    from app.persistence.repository import AnalysisJobRepository

    job_id = uuid4()
    with session_factory.begin() as session:
        AnalysisJobRepository(session).create(
            job_id=job_id, source="upload", audio_path=f"{uuid4()}.wav"
        )
    return job_id


@pytest.fixture
def analysis_result():
    from app.analysis.models import AnalysisResult, Finding

    return AnalysisResult(
        summary="Обсудили условия",
        strengths=[Finding(description="Уточнил цель", quote="Какая цель?")],
        mistakes=[],
        recommendations=["Подвести итог"],
    )


@pytest.fixture
def storage(tmp_path):
    from app.storage.local import LocalAudioStorage

    return LocalAudioStorage(tmp_path / "audio", max_audio_size_mb=1)
