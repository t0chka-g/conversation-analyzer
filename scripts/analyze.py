import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.errors import AnalysisError  # noqa: E402
from app.analysis.openai import OpenAIAnalyzer  # noqa: E402
from app.core.config import get_settings  # noqa: E402


async def analyze_file(path: Path) -> str:
    transcript = path.read_text(encoding="utf-8")
    if not transcript.strip():
        raise ValueError("Transcript file is empty")
    async with OpenAIAnalyzer(get_settings()) as analyzer:
        result = await analyzer.analyze(transcript)
    return result.model_dump_json(indent=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a UTF-8 conversation transcript"
    )
    parser.add_argument("transcript", type=Path)
    args = parser.parse_args()
    try:
        print(asyncio.run(analyze_file(args.transcript)))
    except (OSError, UnicodeError, ValueError, AnalysisError) as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
