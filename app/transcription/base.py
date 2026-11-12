from pathlib import Path
from typing import Protocol


class Transcriber(Protocol):
    async def transcribe(self, audio_path: Path) -> str: ...


class TranscriptionError(Exception):
    """Base error for transcription failures."""


class TranscriptionConfigurationError(TranscriptionError):
    """Invalid transcription configuration."""


class TranscriptionProviderError(TranscriptionError):
    """The audio file or transcription provider failed."""


def normalize_text(text: str) -> str:
    return " ".join(text.split())
