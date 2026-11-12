import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.transcription.base import TranscriptionConfigurationError
from app.transcription.cloud import OpenAITranscriber
from app.transcription.factory import create_transcriber
from app.transcription.local import LocalWhisperTranscriber


def test_factory_local_has_no_side_effects_or_key_requirement(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("Provider must not load a model or create a cloud client")

    monkeypatch.setattr("app.transcription.local.WhisperModel", fail)
    monkeypatch.setattr("app.transcription.cloud.AsyncOpenAI", fail)
    assert isinstance(
        create_transcriber(Settings(_env_file=None, openai_api_key=None)),
        LocalWhisperTranscriber,
    )


def test_factory_cloud(monkeypatch):
    class Client:
        close = AsyncMock()

    monkeypatch.setattr(
        "app.transcription.cloud.AsyncOpenAI", lambda **kwargs: Client()
    )
    provider = create_transcriber(
        Settings(_env_file=None, stt_provider="cloud", openai_api_key="key")
    )
    assert isinstance(provider, OpenAITranscriber)
    asyncio.run(provider.aclose())


def test_invalid_configuration():
    with pytest.raises(TranscriptionConfigurationError, match="OPENAI_API_KEY"):
        create_transcriber(
            Settings(_env_file=None, stt_provider="cloud", openai_api_key="")
        )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, stt_provider="other")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, whisper_cpu_threads=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_stt_timeout=0)
    with pytest.raises(TranscriptionConfigurationError):
        create_transcriber(Settings.model_construct(stt_provider="invalid"))
