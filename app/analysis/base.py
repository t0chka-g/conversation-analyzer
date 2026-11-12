from typing import Protocol

from app.analysis.models import AnalysisResult


class Analyzer(Protocol):
    async def analyze(self, transcript: str) -> AnalysisResult: ...
