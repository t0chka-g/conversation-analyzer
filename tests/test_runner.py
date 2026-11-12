import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

from app.application.create_job import create_job
from app.application.runner import ProcessingRuntime, run_analysis_job
from app.core.config import Settings
from app.main import create_app
from app.persistence.models import JobStatus
from app.persistence.repository import AnalysisJobRepository
from scripts.process_job import process_job


@pytest.mark.parametrize("provider", ["local", "cloud"])
def test_runtime_reuses_providers_and_closes_clients(
    monkeypatch, session_factory, storage, pipeline_case, analysis_result, provider
):
    case = pipeline_case
    transcriber = SimpleNamespace(
        transcribe=case.transcriber.transcribe, aclose=AsyncMock()
    )
    analyzer = SimpleNamespace(analyze=case.analyzer.analyze, aclose=AsyncMock())
    transcriber_factory = Mock(return_value=transcriber)
    analyzer_factory = Mock(return_value=analyzer)
    monkeypatch.setattr(
        "app.application.runner._create_transcriber", transcriber_factory
    )
    monkeypatch.setattr("app.application.runner._create_analyzer", analyzer_factory)
    engine = session_factory.kw["bind"]
    monkeypatch.setattr(
        "app.application.runner.create_database_engine", lambda _: engine
    )
    settings = Settings(stt_provider=provider, audio_storage_dir=storage.directory)

    async def run():
        async with ProcessingRuntime(settings) as runtime:
            transcriber_factory.assert_not_called()
            first, second = await asyncio.gather(
                runtime.get_pipeline(), runtime.get_pipeline()
            )
            assert first is second
            tasks = BackgroundTasks()
            tasks.add_task(run_analysis_job, case.job_id, runtime=runtime)
            await tasks()
            # A second execution uses the same resources and skips the terminal job.
            await run_analysis_job(case.job_id, runtime=runtime)
        transcriber.aclose.assert_awaited_once()
        analyzer.aclose.assert_awaited_once()

    asyncio.run(run())
    transcriber_factory.assert_called_once_with(settings)
    analyzer_factory.assert_called_once_with(settings)
    case.transcriber.transcribe.assert_awaited_once()
    case.analyzer.analyze.assert_awaited_once()


def test_partial_initialization_closes_owned_resources(monkeypatch):
    analyzer = SimpleNamespace(aclose=AsyncMock())
    engine = SimpleNamespace(dispose=Mock())
    monkeypatch.setattr(
        "app.application.runner.create_database_engine", lambda _: engine
    )
    monkeypatch.setattr("app.application.runner._create_analyzer", lambda _: analyzer)

    def broken(_):
        raise RuntimeError("secret configuration")

    monkeypatch.setattr("app.application.runner._create_transcriber", broken)

    async def run():
        async with ProcessingRuntime(Settings()) as runtime:
            with pytest.raises(RuntimeError):
                await runtime.get_pipeline()

    asyncio.run(run())
    analyzer.aclose.assert_awaited_once()
    engine.dispose.assert_called_once()


def test_lifespan_is_lazy_without_api_keys(monkeypatch):
    def forbidden(_):
        pytest.fail("health must not create providers")

    monkeypatch.setattr("app.application.runner._create_transcriber", forbidden)
    monkeypatch.setattr("app.application.runner._create_analyzer", forbidden)
    with TestClient(create_app()) as client:
        assert isinstance(client.app.state.processing_runtime, ProcessingRuntime)
        assert client.get("/health").json() == {"status": "ok"}


def test_runner_logs_no_sensitive_error(pipeline_case, caplog):
    pipeline_case.analyzer.analyze.side_effect = RuntimeError("secret text")
    runtime = SimpleNamespace(
        get_pipeline=AsyncMock(return_value=pipeline_case.pipeline)
    )
    with pytest.raises(RuntimeError):
        asyncio.run(run_analysis_job(pipeline_case.job_id, runtime=runtime))
    assert "stage=runner" in caplog.text and "secret" not in caplog.text


def test_cli_processes_existing_uuid(
    pipeline_case, session_factory, monkeypatch, capsys
):
    case = pipeline_case
    runtime = SimpleNamespace(get_pipeline=AsyncMock(return_value=case.pipeline))

    class RuntimeContext:
        async def __aenter__(self):
            return runtime

        async def __aexit__(self, *_):
            pass

    monkeypatch.setattr(
        "scripts.process_job.ProcessingRuntime", lambda _: RuntimeContext()
    )
    assert asyncio.run(process_job(case.job_id)) == 0
    assert f"{case.job_id}: COMPLETED" in capsys.readouterr().out
    with session_factory() as session:
        assert (
            AnalysisJobRepository(session).get_by_id(case.job_id).status
            == JobStatus.COMPLETED
        )


def test_real_local_factory_reuses_one_lazy_model_across_jobs(
    pipeline_case, session_factory, storage, monkeypatch
):
    case = pipeline_case
    second_id = create_job(
        BytesIO(b"second audio"),
        filename="second.wav",
        source="upload",
        storage=storage,
        session_factory=session_factory,
    )
    model = Mock()
    model.transcribe.side_effect = lambda *args, **kwargs: (
        iter([SimpleNamespace(text="Какая цель?")]),
        None,
    )
    model_factory = Mock(return_value=model)
    monkeypatch.setattr("app.transcription.local.WhisperModel", model_factory)
    analyzer = SimpleNamespace(analyze=case.analyzer.analyze, aclose=AsyncMock())
    monkeypatch.setattr("app.application.runner._create_analyzer", lambda _: analyzer)
    monkeypatch.setattr(
        "app.application.runner.create_database_engine",
        lambda _: session_factory.kw["bind"],
    )

    async def run():
        async with ProcessingRuntime(
            Settings(audio_storage_dir=storage.directory)
        ) as runtime:
            await runtime.get_pipeline()
            model_factory.assert_not_called()
            await run_analysis_job(case.job_id, runtime=runtime)
            await run_analysis_job(second_id, runtime=runtime)

    asyncio.run(run())
    model_factory.assert_called_once()
    assert model.transcribe.call_count == 2
    assert analyzer.analyze.await_count == 2
