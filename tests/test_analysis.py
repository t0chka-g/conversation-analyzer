import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openai import APIError, APITimeoutError
from pydantic import ValidationError

from app.analysis.errors import (
    AnalysisConfigurationError,
    AnalysisProviderError,
    AnalysisValidationError,
)
from app.analysis.grounding import validate_quotes
from app.analysis.models import AnalysisResult, Finding
from app.analysis.openai import OpenAIAnalyzer
from app.analysis.prompts import DEFAULT_PROMPT, load_prompt
from app.core.config import Settings


def result(quote="Привет"):
    return AnalysisResult(
        summary="Краткий разговор",
        strengths=[Finding(description="Приветствие", quote=quote)],
        mistakes=[],
        recommendations=["Уточнить цель"],
    )


def fake_client(parsed=None, refusal=None, error=None):
    parse = AsyncMock()
    if error:
        parse.side_effect = error
    else:
        parse.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal))
            ]
        )
    client = Mock()
    client.beta.chat.completions.parse = parse
    return client


def test_models_validate_fields():
    assert result().strengths[0].quote == "Привет"
    for payload in (
        {
            "summary": "x",
            "strengths": [],
            "mistakes": [],
            "recommendations": [],
            "other": 1,
        },
        {"summary": "x", "strengths": [], "mistakes": []},
        {"summary": 42, "strengths": [], "mistakes": [], "recommendations": []},
        {"summary": " ", "strengths": [], "mistakes": [], "recommendations": []},
        {"summary": "x", "strengths": [], "mistakes": [], "recommendations": [" "]},
    ):
        with pytest.raises(ValidationError):
            AnalysisResult.model_validate(payload)
    with pytest.raises(ValidationError):
        Finding(description="valid", quote="hello", extra="forbidden")
    with pytest.raises(ValidationError):
        Finding(description="valid")


def test_prompt_loading(tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("  Инструкция  ", encoding="utf-8")
    assert load_prompt(prompt).strip() == "Инструкция"
    prompt.write_text("замена", encoding="utf-8")
    assert load_prompt(prompt).strip() == "Инструкция"
    with pytest.raises(AnalysisConfigurationError):
        load_prompt(tmp_path / "missing.md")
    blank = tmp_path / "blank.md"
    blank.write_text(" \n", encoding="utf-8")
    with pytest.raises(AnalysisConfigurationError):
        load_prompt(blank)
    monkeypatch.chdir(tmp_path)
    assert load_prompt(DEFAULT_PROMPT).strip()


def test_grounding():
    validate_quotes(result(), "Привет, как дела?")
    validate_quotes(result("ПРИВЕТ   как"), "Привет\n как дела?")
    validate_quotes(result(None), "Любой текст")
    with pytest.raises(AnalysisValidationError, match="quote"):
        validate_quotes(result("Несуществующая цитата"), "Привет")


def test_analyzer_passes_messages_and_model():
    client = fake_client(result())
    analyzer = OpenAIAnalyzer(Settings(openai_llm_model="test-model"), client)
    assert asyncio.run(analyzer.analyze("Привет")) == result()
    kwargs = client.beta.chat.completions.parse.call_args.kwargs
    assert kwargs["model"] == "test-model"
    assert kwargs["response_format"] is AnalysisResult
    assert kwargs["messages"] == [
        {"role": "system", "content": load_prompt()},
        {"role": "user", "content": "Привет"},
    ]


@pytest.mark.parametrize(
    ("client", "error_type"),
    [
        (
            fake_client(error=APIError("failed", request=Mock(), body=None)),
            AnalysisProviderError,
        ),
        (fake_client(error=APITimeoutError(request=Mock())), AnalysisProviderError),
        (fake_client(refusal="no"), AnalysisProviderError),
        (fake_client(), AnalysisValidationError),
        (fake_client(result("wrong")), AnalysisValidationError),
        (fake_client(error=ValueError("bad parse")), AnalysisValidationError),
    ],
)
def test_analyzer_failures(client, error_type):
    analyzer = OpenAIAnalyzer(Settings(), client)
    with pytest.raises(error_type):
        asyncio.run(analyzer.analyze("Привет"))


def test_empty_transcript_never_calls_provider():
    client = fake_client(result())
    analyzer = OpenAIAnalyzer(Settings(), client)
    with pytest.raises(AnalysisValidationError):
        asyncio.run(analyzer.analyze("   "))
    client.beta.chat.completions.parse.assert_not_called()


def test_configuration_and_client_lifecycle(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(_env_file=None)
    assert settings.openai_api_key is None
    assert settings.openai_base_url is None
    with pytest.raises(AnalysisConfigurationError, match="OPENAI_API_KEY"):
        OpenAIAnalyzer(settings)
    assert settings.openai_llm_model == "gpt-4o-mini"
    assert Settings(openai_base_url="").openai_base_url is None
    with pytest.raises(ValidationError):
        Settings(openai_llm_timeout=0)
    with pytest.raises(ValidationError):
        Settings(openai_llm_max_retries=-1)

    created = {}

    def capture_client(**kwargs):
        created.update(kwargs)
        client = fake_client(result())
        client.close = AsyncMock()
        return client

    monkeypatch.setattr("app.analysis.openai.AsyncOpenAI", capture_client)
    analyzer = OpenAIAnalyzer(
        Settings(
            _env_file=None,
            openai_api_key="secret",
            openai_base_url="https://example.test/v1",
        )
    )
    assert created["api_key"] == "secret"
    assert created["base_url"] == "https://example.test/v1"
    asyncio.run(analyzer.aclose())

    client = fake_client(result())
    client.close = AsyncMock()
    analyzer = OpenAIAnalyzer(Settings(), client)
    asyncio.run(analyzer.aclose())
    client.close.assert_not_called()


def test_prompt_path_is_package_relative():
    assert Path(DEFAULT_PROMPT).is_file()
