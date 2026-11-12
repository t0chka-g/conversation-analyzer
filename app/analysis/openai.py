from openai import APITimeoutError, AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from app.analysis.base import Analyzer
from app.analysis.errors import (
    AnalysisConfigurationError,
    AnalysisProviderError,
    AnalysisValidationError,
)
from app.analysis.grounding import validate_quotes
from app.analysis.models import AnalysisResult
from app.analysis.prompts import load_prompt
from app.core.config import Settings


class OpenAIAnalyzer:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        key = settings.openai_api_key
        if client is None and (key is None or not key.get_secret_value().strip()):
            raise AnalysisConfigurationError(
                "OPENAI_API_KEY is required for LLM analysis"
            )
        self._prompt = load_prompt()
        self._settings = settings
        self._client = client or AsyncOpenAI(
            api_key=key.get_secret_value() if key else None,
            base_url=settings.openai_base_url,
            timeout=settings.openai_llm_timeout,
            max_retries=settings.openai_llm_max_retries,
        )
        self._owns_client = client is None

    async def analyze(self, transcript: str) -> AnalysisResult:
        if not isinstance(transcript, str) or not transcript.strip():
            raise AnalysisValidationError("Transcript must be nonempty text")
        try:
            completion = await self._client.beta.chat.completions.parse(
                model=self._settings.openai_llm_model,
                messages=[
                    {"role": "system", "content": self._prompt},
                    {"role": "user", "content": transcript},
                ],
                response_format=AnalysisResult,
            )
        except APITimeoutError as exc:
            raise AnalysisProviderError("OpenAI analysis timed out") from exc
        except OpenAIError as exc:
            raise AnalysisProviderError("OpenAI analysis request failed") from exc
        except (ValidationError, ValueError) as exc:
            raise AnalysisValidationError(
                "OpenAI structured response is invalid"
            ) from exc

        if not completion.choices:
            raise AnalysisValidationError("OpenAI returned no analysis choice")
        message = completion.choices[0].message
        if message.refusal:
            raise AnalysisProviderError("OpenAI refused to analyze the transcript")
        result = message.parsed
        if not isinstance(result, AnalysisResult):
            raise AnalysisValidationError(
                "OpenAI returned no valid structured analysis"
            )
        validate_quotes(result, transcript)
        return result

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.close()

    async def __aenter__(self) -> "OpenAIAnalyzer":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def create_analyzer(settings: Settings) -> Analyzer:
    return OpenAIAnalyzer(settings)
