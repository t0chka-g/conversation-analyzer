from app.core.config import Settings
from app.transcription.base import Transcriber, TranscriptionConfigurationError
from app.transcription.cloud import OpenAITranscriber
from app.transcription.local import LocalWhisperTranscriber


def create_transcriber(settings: Settings) -> Transcriber:
    if settings.stt_provider == "local":
        return LocalWhisperTranscriber(settings)
    if settings.stt_provider == "cloud":
        return OpenAITranscriber(settings)
    raise TranscriptionConfigurationError("STT_PROVIDER must be 'local' or 'cloud'.")
