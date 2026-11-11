# Environment
- Debian GNU/Linux 13 (cloud Linux x86_64), Bash. Windows/WSL are not available here.
- Use the existing project checkout, not a new worktree.
- Python 3.12.14, uv 0.13.5 are already installed and on PATH.
- Use `uv run` with the existing `.venv`; activation is unnecessary.
- `uv.toml` puts the cache in `.cache/uv` because the default home cache is read-only in this cloud sandbox.

# Development commands
Run from the project root:
- Install: `uv sync --locked --dev` (initial lock creation: `uv sync --dev`).
- Server: `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload`.
- Readiness: `curl -fsS http://127.0.0.1:8000/health` must return `{"status":"ok"}`.
- Tests: `uv run pytest`.
- Lint: `uv run ruff check .`.
- Format: `uv run ruff format .`; verify with `uv run ruff format --check .`.
- Dependencies: `uv add <package>` or `uv add --dev <package>`; retain `uv.lock`.
- STT CLI: `uv run python scripts/transcribe.py path/to/audio.wav`. Local weights load lazily into the system Hugging Face cache; reuse the provider and close the cloud client with `aclose()`. Tests must not download models or call external APIs.
- Optional local config: copy `.env.example` to `.env`; environment variables override `.env`.

# Architecture conventions
Modular FastAPI monolith with `create_app()` in `app/main.py`.
Configuration uses pydantic-settings and cached `get_settings()`; logging uses standard Python logging.
Keep external providers isolated when introduced. Use minimal abstractions; do not add microservices or infrastructure without requirements.
Bootstrap, `/health` and standalone STT exist; LLM, storage, uploads and frontend belong to later stages.

# Agent rules
- Reuse Python, uv and the single `.venv`; do not reinstall or switch environments without a demonstrated need.
- Do not run `uv lock --upgrade` without a reason.
- Run relevant tests and Ruff after code changes; report only checks actually executed.
- Tests must not depend on external services. Clear the settings cache when changing configuration in tests.
- Do not create empty architecture layers for future features.
- Do not commit unless explicitly requested.
