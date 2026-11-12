import asyncio
import threading
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.transcription.base import TranscriptionProviderError
from app.transcription.local import LocalWhisperTranscriber


def test_lazy_model_reuse_and_worker_thread(monkeypatch, tmp_path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    main_thread = threading.get_ident()
    calls = []

    class Model:
        def __init__(self, *args, **kwargs):
            calls.append((args, kwargs, threading.get_ident()))

        def transcribe(self, path, *, language):
            assert path == str(audio) and language == "ru"
            assert threading.get_ident() != main_thread

            def segments():
                assert threading.get_ident() != main_thread
                yield SimpleNamespace(text="  Привет  ")
                yield SimpleNamespace(text="  мир! \n")

            return segments(), None

    monkeypatch.setattr("app.transcription.local.WhisperModel", Model)
    provider = LocalWhisperTranscriber(Settings(_env_file=None))
    assert calls == []

    async def run():
        assert await provider.transcribe(audio) == "Привет мир!"
        assert await provider.transcribe(audio) == "Привет мир!"

    asyncio.run(run())
    assert len(calls) == 1
    assert calls[0] == (
        ("small",),
        {"device": "cpu", "compute_type": "int8", "cpu_threads": 4},
        calls[0][2],
    )
    assert calls[0][2] != main_thread


def test_empty_and_lazy_generator_error(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.touch()

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            return iter([]), None

    monkeypatch.setattr("app.transcription.local.WhisperModel", Model)
    provider = LocalWhisperTranscriber(Settings(_env_file=None))
    assert asyncio.run(provider.transcribe(audio)) == ""

    def fail(*args, **kwargs):
        def segments():
            raise ValueError("inference failed")
            yield

        return segments(), None

    monkeypatch.setattr(provider._model, "transcribe", fail)
    with pytest.raises(TranscriptionProviderError) as err:
        asyncio.run(provider.transcribe(audio))
    assert isinstance(err.value.__cause__, ValueError)


def test_serialization_survives_cancellation(monkeypatch, tmp_path):
    audio = tmp_path / "a.wav"
    audio.touch()
    entered, release = threading.Event(), threading.Event()
    active = 0
    max_active = 0

    class Model:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, *args, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            entered.set()
            assert release.wait(5)
            active -= 1
            return iter([]), None

    monkeypatch.setattr("app.transcription.local.WhisperModel", Model)
    provider = LocalWhisperTranscriber(Settings(_env_file=None))

    async def run():
        first = asyncio.create_task(provider.transcribe(audio))
        assert await asyncio.to_thread(entered.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(provider.transcribe(audio))
        await asyncio.sleep(0.05)
        release.set()
        await second

    asyncio.run(run())
    assert max_active == 1


def test_missing_audio_does_not_load_model(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        pytest.fail("Model must not load")

    monkeypatch.setattr("app.transcription.local.WhisperModel", fail)
    with pytest.raises(TranscriptionProviderError) as err:
        asyncio.run(
            LocalWhisperTranscriber(Settings(_env_file=None)).transcribe(
                tmp_path / "missing"
            )
        )
    assert isinstance(err.value.__cause__, FileNotFoundError)
