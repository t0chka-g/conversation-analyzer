from pathlib import Path

from openai import AsyncOpenAI

from app.core.config import Settings
from app.transcription.base import (
    TranscriptionConfigurationError,
    TranscriptionProviderError,
    normalize_text,
)


class OpenAITranscriber:
    def __init__(self, settings: Settings):
        if (
            not settings.openai_api_key
            or not settings.openai_api_key.get_secret_value().strip()
        ):
            raise TranscriptionConfigurationError(
                "OPENAI_API_KEY is required for cloud transcription."
            )
        self._model = settings.openai_stt_model
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            timeout=settings.openai_stt_timeout,
        )

    async def transcribe(self, audio_path: Path) -> str:
        try:
            with audio_path.open("rb") as audio:
                result = await self._client.audio.transcriptions.create(
                    model=self._model, file=audio
                )
            return normalize_text(result.text)
        except Exception as exc:
            raise TranscriptionProviderError(
                "Cloud transcription failed; check the audio file and provider."
            ) from exc

    async def aclose(self) -> None:
        await self._client.close()
