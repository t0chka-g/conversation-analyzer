from app.analysis.errors import AnalysisValidationError
from app.analysis.models import AnalysisResult


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_quotes(result: AnalysisResult, transcript: str) -> None:
    normalized = _normalize(transcript)
    for finding in (*result.strengths, *result.mistakes):
        if finding.quote is not None and _normalize(finding.quote) not in normalized:
            raise AnalysisValidationError(
                "Analysis contains a quote absent from transcript"
            )
