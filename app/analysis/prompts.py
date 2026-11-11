from functools import lru_cache
from pathlib import Path

from app.analysis.errors import AnalysisConfigurationError

DEFAULT_PROMPT = Path(__file__).resolve().parent / "prompts" / "default.md"


@lru_cache
def load_prompt(path: Path = DEFAULT_PROMPT) -> str:
    try:
        prompt = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AnalysisConfigurationError(
            f"Cannot load analysis prompt: {path}"
        ) from exc
    if not prompt.strip():
        raise AnalysisConfigurationError(f"Analysis prompt is empty: {path}")
    return prompt
