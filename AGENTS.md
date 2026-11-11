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
- Initialize SQLite: `uv run python scripts/init_db.py` (explicit, idempotent; no schema migrations).
- Process an existing job: `uv run python scripts/process_job.py <job_id>`; manual only, can invoke paid STT/LLM and download local weights.
- STT CLI: `uv run python scripts/transcribe.py path/to/audio.wav`. Local weights load lazily into the system Hugging Face cache; reuse the provider and close the cloud client with `aclose()`. Tests must not download models or call external APIs.
- Optional local config: copy `.env.example` to `.env`; environment variables override `.env`.

# Architecture conventions
Modular FastAPI monolith with `create_app()` in `app/main.py`.
Configuration uses pydantic-settings and cached `get_settings()`; logging uses standard Python logging.
Keep external providers isolated when introduced. Use minimal abstractions; do not add microservices or infrastructure without requirements.
Bootstrap, `/health`, standalone STT/LLM, SQLite persistence, local audio storage, ProcessingPipeline and REST uploads/results exist; frontend belongs to a later stage.
Repository operations receive a session and never commit. Use `session_factory.begin()` for short transactions, keep STT/LLM calls outside them, and commit claims/transcripts before provider work. State transitions use conditional SQL updates, never read-then-write claims. Use `create_job()` for audio ingestion and DB-error compensation; audio keys are relative, timestamps are UTC. Tests use temporary SQLite/audio paths.
LLM Analyzer lives in `app/analysis`; its system prompt is `app/analysis/prompts/default.md`. Keep transcript content separate from system instructions and verify quoted evidence against input.
Run the analyzer manually with `uv run python scripts/analyze.py transcript.txt`, or process an existing job through the pipeline CLI; both can make paid LLM calls.
Pipeline DB operations use `anyio.to_thread.run_sync` with a fresh session per operation; never share sessions across threads or hold them during provider calls. Use `ProcessingRuntime` in an async context for reusable providers and cleanup; FastAPI lifespan exposes it as `app.state.processing_runtime`. HTTP BackgroundTasks uses `run_background_analysis(job_id, runtime=...)`, which calls the existing runner and keeps safely logged errors out of ASGI tracebacks; standalone `run_analysis_job` still propagates. Never pass a request-scoped session. Pipeline error stages are `transcription`/`analysis`; repository aliases `stt`/`llm` remain compatible. BackgroundTasks is in-process without recovery or durable delivery.
REST routes: `POST /api/v1/analyses` (multipart `file`, 202 + Location) and `GET /api/v1/analyses/{job_id}`. Keep handlers synchronous for ingestion/DB work in FastAPI's thread pool. Lifespan owns shared engine/session factory/storage; runtime borrows them and owns providers. Upload validation uses typed AudioStorage errors; map them to 400/413/415 without inspecting exception text. Format checks cover extensions, not codecs. API tests use fake providers, temporary SQLite/audio and separate response-contract checks from real background execution because TestClient waits for BackgroundTasks.

# Agent rules
- Reuse Python, uv and the single `.venv`; do not reinstall or switch environments without a demonstrated need.
- Do not run `uv lock --upgrade` without a reason.
- Run relevant tests and Ruff after code changes; report only checks actually executed.
- Tests must not depend on external services. Clear the settings cache when changing configuration in tests.
- Do not create empty architecture layers for future features.
- Do not commit unless explicitly requested.
