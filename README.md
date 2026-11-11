# Conversation Analyzer

MVP backend для записи и AI-анализа разговоров. Архитектура — модульный монолит FastAPI; STT-провайдеры и LLM Analyzer изолированы от HTTP и приложения.

Стек: Python 3.12+, uv, FastAPI, Uvicorn, pydantic-settings, OpenAI SDK; pytest, HTTPX и Ruff для разработки.

## Установка

В облачном окружении Debian 13/Bash Python и uv уже доступны. Используется одна `.venv`; активация не нужна.

```bash
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
  transcription/  # контракт, локальный и облачный STT, фабрика
  analysis/       # LLM Analyzer, модели, промпт и проверка цитат
scripts/transcribe.py # проверка STT из командной строки
scripts/analyze.py    # анализ готовой транскрипции
tests/             # изолированные тесты приложения и настроек
AGENTS.md          # рабочие инструкции для следующих агентов
```

Этап 1: окружение, application factory, настройки, логирование, healthcheck и тесты.
Этап 2: локальная и облачная транскрибация через единый асинхронный контракт.
Этап 3: независимый LLM-анализ готовой транскрипции. БД, загрузка аудио, фоновая обработка и frontend ещё не реализованы.


## Транскрибация

По умолчанию используется `STT_PROVIDER=local`: faster-whisper загружает `small` при первом вызове, на CPU с `int8` и четырьмя потоками; язык — русский. Настройки `WHISPER_MODEL`, `WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`, `WHISPER_LANGUAGE`, `WHISPER_CPU_THREADS` описаны в `.env.example`. `WHISPER_MODEL` может содержать путь к локальной директории модели в формате CTranslate2. Один экземпляр провайдера повторно использует модель и выполняет одно распознавание за раз. На CPU загрузка и распознавание могут занять заметное время и оперативную память.

```bash
uv run python scripts/transcribe.py path/to/audio.wav
```

При первом использовании имени модели faster-whisper скачивает веса в системный кэш Hugging Face (обычно `~/.cache/huggingface/hub` в Linux или каталог кэша пользователя в Windows; расположение зависит от `HF_HOME` и `HF_HUB_CACHE`). В репозиторий веса не сохраняются. Для offline-работы заранее выполните локальную транскрибацию с нужной моделью при доступном интернете, либо заранее подготовьте директорию модели CTranslate2 и укажите её в `WHISPER_MODEL`. На целевой машине модель и все файлы должны быть доступны до отключения сети.

Для облачного режима задайте `STT_PROVIDER=cloud` и `OPENAI_API_KEY` в окружении или локальном `.env`. `OPENAI_STT_MODEL` выбирает модель (по умолчанию `gpt-4o-mini-transcribe`), `OPENAI_STT_TIMEOUT` — таймаут в секундах. Облачные запросы требуют сети и могут быть платными. Локальному режиму ключ не нужен.

В коде создайте провайдер через `create_transcriber(settings)` и повторно используйте его. У облачного провайдера после завершения работы вызовите `await provider.aclose()`. Оба провайдера возвращают строку; API загрузки и обработка аудио не входят в этот этап.

## Анализ готовой транскрипции

`app.analysis` принимает готовую строку и возвращает Pydantic `AnalysisResult`: `summary`, `strengths`, `mistakes`, `recommendations`. Каждый элемент `strengths` и `mistakes` содержит `description` и дословную `quote` либо `null`. Используются OpenAI Structured Outputs и модель `gpt-4o-mini` по умолчанию.

Задайте `OPENAI_API_KEY` в окружении или `.env` и запустите ручную проверку:

```bash
uv run python scripts/analyze.py transcript.txt
```

CLI читает UTF-8 файл и выводит JSON. `OPENAI_LLM_MODEL` задаёт модель, `OPENAI_LLM_TIMEOUT` — таймаут в секундах, `OPENAI_LLM_MAX_RETRIES` — число повторов (0–5). Ключ не нужен для локального STT или импорта FastAPI; при создании OpenAI Analyzer без ключа возникает понятная ошибка.

Системный промпт находится в `app/analysis/prompts/default.md`. Его можно заменить без изменения Python-кода; новый процесс загрузит новый текст. Транскрипция передаётся отдельно от системных инструкций. Grounding проверяет наличие цитат в исходном тексте с нормализацией регистра и пробелов, но не доказывает смысловую верность каждого вывода. Схема результата предварительная и потребует согласования с промптом заказчика. Автоматические тесты не вызывают LLM и не требуют сети.
