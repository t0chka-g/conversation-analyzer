import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app


def test_create_app():
    application = create_app()
    assert isinstance(application, FastAPI)
    assert application is not create_app()
    assert application.title == "Conversation Analyzer"


def test_health_status(client):
    assert client.get("/health").status_code == 200


def test_health_json(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Test API")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    settings = get_settings()
    assert (
        settings.app_name,
        settings.app_env,
        settings.debug,
        settings.log_level,
    ) == ("Test API", "test", True, "DEBUG")
    assert get_settings() is settings
    assert create_app().title == "Test API"
    assert create_app().debug is True


def test_settings_from_dotenv(tmp_path):
    (tmp_path / ".env").write_text(
        "APP_NAME=Dotenv API\nAPP_ENV=local\nDEBUG=true\nLOG_LEVEL=WARNING\n"
    )
    settings = get_settings()
    assert settings.app_name == "Dotenv API"
    assert settings.app_env == "local"
    assert settings.debug is True
    assert settings.log_level == "WARNING"


@pytest.mark.parametrize(
    ("name", "value"),
    [("APP_ENV", "development"), ("DEBUG", "maybe"), ("LOG_LEVEL", "INVALID")],
)
def test_invalid_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings()
