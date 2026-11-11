import argparse
import asyncio
import sys
from pathlib import Path

# Allow direct execution from the repository root without installing the project.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.transcription.base import TranscriptionError  # noqa: E402
from app.transcription.factory import create_transcriber  # noqa: E402


async def run(audio_path: Path) -> None:
    provider = create_transcriber(get_settings())
    try:
        print(await provider.transcribe(audio_path))
    finally:
        close = getattr(provider, "aclose", None)
        if close is not None:
            await close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe an audio file")
    parser.add_argument("audio_path", type=Path)
    args = parser.parse_args()
    if not args.audio_path.is_file():
        parser.error("audio file does not exist or is not a regular file")
    try:
        asyncio.run(run(args.audio_path))
    except (TranscriptionError, ValidationError) as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
