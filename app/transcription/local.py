import asyncio
import threading
from pathlib import Path

from faster_whisper import WhisperModel

from app.core.config import Settings
from app.transcription.base import TranscriptionProviderError, normalize_text


class LocalWhisperTranscriber:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._model = None
        # A thread lock remains held if the awaiting coroutine is cancelled.
        self._lock = threading.Lock()

    async def transcribe(self, audio_path: Path) -> str:
        return await asyncio.to_thread(self._transcribe, audio_path)

    def _transcribe(self, audio_path: Path) -> str:
        with self._lock:
            try:
                # Open the file to check access before downloading/loading a model.
                with audio_path.open("rb"):
                    pass
                if self._model is None:
                    self._model = WhisperModel(
                        self._settings.whisper_model,
                        device=self._settings.whisper_device,
                        compute_type=self._settings.whisper_compute_type,
                        cpu_threads=self._settings.whisper_cpu_threads,
                    )
                segments, _ = self._model.transcribe(
                    str(audio_path), language=self._settings.whisper_language
                )
                return normalize_text(" ".join(segment.text for segment in segments))
            except Exception as exc:
                raise TranscriptionProviderError(
                    "Local transcription failed; check the audio file and model."
                ) from exc
