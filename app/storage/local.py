import re
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


class AudioStorageError(Exception):
    """Invalid audio input or failure accessing local audio storage."""


class LocalAudioStorage:
    EXTENSIONS = frozenset({".wav", ".mp3", ".webm", ".m4a"})
    CHUNK_SIZE = 64 * 1024

    def __init__(self, directory: Path, max_audio_size_mb: int = 20) -> None:
        if max_audio_size_mb <= 0:
            raise ValueError("max_audio_size_mb must be positive")
        self.directory = Path(directory).resolve()
        self.max_bytes = max_audio_size_mb * 1024 * 1024

    def save(self, stream: BinaryIO, *, filename: str) -> str:
        """The caller owns the stream. Only bounded binary reads are performed."""
        if (
            not isinstance(filename, str)
            or any(character in filename for character in ("/", "\\", ":", "\x00"))
            or Path(filename).suffix.lower() not in self.EXTENSIONS
        ):
            raise AudioStorageError("Unsupported audio filename or extension")
        key = f"{uuid4()}{Path(filename).suffix.lower()}"
        final_path = self.directory / key
        temporary_path = self.directory / f".{uuid4()}.tmp"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            total = 0
            with temporary_path.open("xb") as destination:
                while True:
                    chunk = stream.read(
                        min(self.CHUNK_SIZE, self.max_bytes - total + 1)
                    )
                    if not isinstance(chunk, bytes):
                        raise AudioStorageError("Audio stream must return bytes")
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_bytes:
                        raise AudioStorageError(
                            "Audio exceeds the configured size limit"
                        )
                    destination.write(chunk)
                if total == 0:
                    raise AudioStorageError("Audio file is empty")
            temporary_path.replace(final_path)
            return key
        except Exception as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                raise AudioStorageError(
                    "Audio write and temporary cleanup failed"
                ) from None
            if isinstance(exc, AudioStorageError):
                raise
            raise AudioStorageError("Unable to save audio") from None

    def get_path(self, key: str) -> Path:
        """Resolve only generated keys; reject traversal and symlink escapes."""
        if not isinstance(key, str) or not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
            r"\.(wav|mp3|webm|m4a)",
            key,
        ):
            raise AudioStorageError("Invalid audio storage key")
        path = self.directory / key
        try:
            if path.is_symlink() or path.resolve().parent != self.directory:
                raise AudioStorageError("Audio path escapes storage directory")
        except OSError:
            raise AudioStorageError("Unable to resolve audio path") from None
        return path

    def delete(self, key: str) -> None:
        path = self.get_path(key)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise AudioStorageError("Unable to delete audio") from None
