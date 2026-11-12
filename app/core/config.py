from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Conversation Analyzer"
    app_env: Literal["local", "test", "production"] = "local"
    debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    stt_provider: Literal["local", "cloud"] = "local"
    whisper_model: str = Field(default="small", min_length=1)
    whisper_device: Literal["cpu"] = "cpu"
    whisper_compute_type: str = Field(default="int8", min_length=1)
    whisper_language: str = Field(default="ru", min_length=1)
    whisper_cpu_threads: int = Field(default=4, gt=0)
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    openai_stt_model: str = Field(default="gpt-4o-mini-transcribe", min_length=1)
    openai_stt_timeout: float = Field(default=120, gt=0, allow_inf_nan=False)
    openai_llm_model: str = Field(default="gpt-4o-mini", min_length=1)
    openai_llm_timeout: float = Field(default=120, gt=0, allow_inf_nan=False)
    openai_llm_max_retries: int = Field(default=1, ge=0, le=5)

    database_url: str = "sqlite:///./data/app.db"
    audio_storage_dir: Path = Path("./data/audio")
    max_audio_size_mb: int = Field(default=20, gt=0)

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def empty_openai_base_url(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def sqlite_database(cls, value: str) -> str:
        try:
            url = make_url(value)
        except ArgumentError:
            raise ValueError("DATABASE_URL must be a valid SQLite URL") from None
        if url.drivername not in {"sqlite", "sqlite+pysqlite"} or any(
            value is not None
            for value in (url.host, url.username, url.password, url.port)
        ):
            raise ValueError("DATABASE_URL must use local synchronous SQLite")
        if url.query:
            raise ValueError("SQLite URI/query options are not supported")
        return value

    @field_validator("audio_storage_dir", mode="before")
    @classmethod
    def nonempty_storage_dir(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("AUDIO_STORAGE_DIR must not be empty")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
