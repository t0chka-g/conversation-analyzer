import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.transcription.base import TranscriptionProviderError
from app.transcription.cloud import OpenAITranscriber


def test_cloud_request_and_close(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"audio")
    create = AsyncMock(return_value=SimpleNamespace(text="  Привет \n мир "))
    close = AsyncMock()
    clients = []

    def client(**kwargs):
        clients.append(kwargs)
        return SimpleNamespace(
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)),
            close=close,
        )

    monkeypatch.setattr("app.transcription.cloud.AsyncOpenAI", client)
    provider = OpenAITranscriber(
        Settings(
            _env_file=None,
            openai_api_key="secret",
            openai_stt_model="test-model",
            openai_stt_timeout=15,
        )
    )

    async def run():
        assert await provider.transcribe(audio) == "Привет мир"
        assert await provider.transcribe(audio) == "Привет мир"
        await provider.aclose()

    asyncio.run(run())
    assert clients == [{"api_key": "secret", "timeout": 15}]
    assert create.call_count == 2
    assert create.call_args.kwargs["model"] == "test-model"
    assert create.call_args.kwargs["file"].name == str(audio)
    close.assert_awaited_once()


def test_cloud_error_is_chained(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.touch()
    create = AsyncMock(side_effect=RuntimeError("API failure"))
    monkeypatch.setattr(
        "app.transcription.cloud.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
        ),
    )
    provider = OpenAITranscriber(Settings(_env_file=None, openai_api_key="secret"))
    with pytest.raises(TranscriptionProviderError) as err:
        asyncio.run(provider.transcribe(audio))
    assert isinstance(err.value.__cause__, RuntimeError)
