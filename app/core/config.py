from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    openai_stt_model: str = Field(default="gpt-4o-mini-transcribe", min_length=1)
    openai_stt_timeout: float = Field(default=120, gt=0, allow_inf_nan=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
