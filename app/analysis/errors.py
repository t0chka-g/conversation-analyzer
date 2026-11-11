class AnalysisError(Exception):
    """Base error for conversation analysis."""


class AnalysisConfigurationError(AnalysisError):
    """Analyzer configuration or prompt is unavailable."""


class AnalysisProviderError(AnalysisError):
    """OpenAI did not return a usable response."""


class AnalysisValidationError(AnalysisError):
    """Input or structured analysis failed validation."""
