# Conversation Analyzer

MVP backend для записи и AI-анализа разговоров. Архитектура — модульный монолит FastAPI; внешние STT/LLM-провайдеры будут изолированы в последующих этапах.

Стек: Python 3.12+, uv, FastAPI, Uvicorn, pydantic-settings; pytest, HTTPX и Ruff для разработки.

## Установка

В облачном окружении Debian 13/Bash Python и uv уже доступны. Используется одна `.venv`; активация не нужна.

```bash
cd /workspace/conversation-analyzer
uv sync --locked --dev
# Необязательно: локальные настройки (не перезаписывайте существующий .env)
cp -n .env.example .env
```

На другой машине нужны Python 3.12+ и uv. Если Python отсутствует: `uv python install 3.12`.
`uv.toml` хранит кэш в `.cache/uv`, доступном для записи в cloud sandbox.
Настройки `APP_NAME`, `APP_ENV`, `DEBUG`, `LOG_LEVEL` читаются из окружения и `.env`; окружение имеет приоритет. Настройки кэшируются до перезапуска процесса.

## Запуск

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Проверка в другом терминале: `curl -fsS http://127.0.0.1:8000/health` → `{"status":"ok"}`.

## Проверки

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

Форматирование: `uv run ruff format .`. Новые зависимости: `uv add <package>` или `uv add --dev <package>`.

## Структура и статус

```text
app/
  main.py          # create_app() и ASGI app
  api/health.py    # GET /health
  core/config.py  # настройки и get_settings()
  core/logging.py # стандартное логирование
tests/             # изолированные тесты приложения и настроек
AGENTS.md          # рабочие инструкции для следующих агентов
```

Этап 1: окружение, application factory, настройки, логирование, healthcheck и тесты.
STT, LLM-анализ, БД, загрузка аудио, фоновая обработка и frontend ещё не реализованы. Внешние сервисы и секреты для этапа 1 не нужны.

Следующий этап — реализация модуля транскрибации аудио (STT).
