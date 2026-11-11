from io import BytesIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.application.create_job import create_job
from app.application.runner import run_background_analysis
from app.persistence.models import AnalysisJob, JobStatus
from app.persistence.repository import AnalysisJobRepository
from app.storage.local import AudioStorageError, LocalAudioStorage

URL = "/api/v1/analyses"


@pytest.mark.parametrize("extension", ["wav", "MP3", "webm", "m4a"])
def test_upload_contract(api_case, captured_background, extension):
    case = api_case
    response = case.client.post(
        URL, files={"file": (f"original.{extension}", b"audio")}
    )
    assert response.status_code == 202
    data = response.json()
    job_id = UUID(data["job_id"])
    assert data == {"job_id": str(job_id), "status": "PENDING"}
    assert response.headers["Location"] == f"{URL}/{job_id}"
    with case.sessions() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        assert job.status == JobStatus.PENDING and job.source == "upload"
        assert "original" not in job.audio_path
        assert case.storage.get_path(job.audio_path).read_bytes() == b"audio"
    assert len(captured_background) == 1
    task = captured_background[0]
    assert task.func is run_background_analysis
    assert task.args == (job_id,)
    assert task.kwargs == {"runtime": case.app.state.processing_runtime}
    case.transcriber_factory.assert_not_called()
    case.analyzer_factory.assert_not_called()
    assert case.client.get(response.headers["Location"]).json() == {
        "job_id": str(job_id),
        "status": "PENDING",
        "transcript": None,
        "analysis": None,
        "error": None,
    }


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        ("audio.wav", b"", 400),
        ("../audio.wav", b"x", 400),
        ("audio.txt", b"x", 415),
        ("audio", b"x", 415),
        ("audio.wav", b"x" * (1024 * 1024 + 1), 413),
    ],
)
def test_invalid_upload(api_case, captured_background, filename, content, expected):
    case = api_case
    # The real byte limit must win even with a deliberately false header.
    response = case.client.post(
        URL, files={"file": (filename, content)}, headers={"Content-Length": "1"}
    )
    assert response.status_code == expected
    assert "detail" in response.json()
    with case.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 0
    assert not list(case.storage.directory.glob("*"))
    assert captured_background == []
    case.transcriber.transcribe.assert_not_awaited()


def test_exact_size_limit_uses_bounded_reads(
    api_case, captured_background, monkeypatch
):
    original = LocalAudioStorage.save
    streams = []

    def save(self, stream, *, filename):
        streams.append(stream)

        class BoundedStream:
            def read(_, size=-1):
                assert 0 < size <= self.CHUNK_SIZE
                return stream.read(size)

        return original(self, BoundedStream(), filename=filename)

    monkeypatch.setattr(LocalAudioStorage, "save", save)
    response = api_case.client.post(URL, files={"file": ("a.wav", b"x" * 1024 * 1024)})
    assert response.status_code == 202
    assert streams[0].closed


@pytest.mark.parametrize("files", [None, {"wrong_field": ("a.wav", b"x")}])
def test_missing_file_is_422(api_case, captured_background, files):
    assert api_case.client.post(URL, files=files).status_code == 422
    assert captured_background == []


def test_storage_failure_is_safe_500(
    api_case, captured_background, monkeypatch, caplog
):
    def fail(*args, **kwargs):
        raise AudioStorageError("secret key and internal audio path")

    monkeypatch.setattr(LocalAudioStorage, "save", fail)
    response = api_case.client.post(URL, files={"file": ("a.wav", b"x")})
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to create analysis job"}
    assert "secret" not in response.text + caplog.text
    assert captured_background == []
    with api_case.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 0


@pytest.mark.parametrize("failure_point", ["flush", "commit"])
def test_creation_failure_compensates_audio(
    api_case, captured_background, monkeypatch, caplog, failure_point
):
    def fail(*args, **kwargs):
        raise SQLAlchemyError("secret DB values")

    if failure_point == "flush":
        monkeypatch.setattr(AnalysisJobRepository, "create", fail)
    else:
        event.listen(api_case.app.state.analysis_sessions, "before_commit", fail)
    try:
        response = api_case.client.post(URL, files={"file": ("a.wav", b"x")})
    finally:
        if failure_point == "commit":
            event.remove(api_case.app.state.analysis_sessions, "before_commit", fail)
    assert response.status_code == 500
    assert "secret" not in response.text + caplog.text
    assert not list(api_case.storage.directory.glob("*"))
    assert captured_background == []
    with api_case.sessions() as session:
        assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 0


@pytest.mark.parametrize("status", list(JobStatus))
def test_get_all_states(api_case, analysis_result, status):
    case = api_case
    job_id = create_job(
        BytesIO(b"audio"),
        filename="a.wav",
        source="upload",
        storage=case.storage,
        session_factory=case.sessions,
    )
    transcript = None
    analysis = None
    error = None
    with case.sessions.begin() as session:
        repository = AnalysisJobRepository(session)
        if status != JobStatus.PENDING:
            repository.claim_pending(job_id)
        if status in {JobStatus.ANALYZING, JobStatus.COMPLETED, JobStatus.FAILED}:
            transcript = "Какая цель?"
            repository.save_transcript(job_id, transcript)
        if status == JobStatus.COMPLETED:
            repository.complete(job_id, analysis_result)
            analysis = analysis_result.model_dump(mode="json")
        if status == JobStatus.FAILED:
            repository.fail(
                job_id, error_stage="analysis", error_code="ANALYSIS_TIMEOUT"
            )
            error = {"stage": "analysis", "code": "ANALYSIS_TIMEOUT"}
    response = case.client.get(f"{URL}/{job_id}")
    assert response.status_code == 200
    assert response.json() == {
        "job_id": str(job_id),
        "status": status,
        "transcript": transcript,
        "analysis": analysis,
        "error": error,
    }
    assert "audio_path" not in response.text and "created_at" not in response.text


def test_unknown_uuid(api_case):
    assert api_case.client.get(f"{URL}/{uuid4()}").status_code == 404


def test_invalid_uuid(api_case):
    assert api_case.client.get(f"{URL}/not-a-uuid").status_code == 422


def test_read_failure_safe_even_with_debug_enabled(api_case, monkeypatch, caplog):
    api_case.app.debug = True

    def broken(*args):
        raise SQLAlchemyError("secret conversation")

    monkeypatch.setattr(AnalysisJobRepository, "get_by_id", broken)
    response = api_case.client.get(f"{URL}/{uuid4()}")
    assert response.status_code == 500
    assert response.json() == {"detail": "Unable to read analysis job"}
    assert "secret" not in response.text + caplog.text


def test_openapi_and_health(api_case):
    response = api_case.client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "202" in schema["paths"][URL]["post"]["responses"]
    assert (
        "multipart/form-data" in schema["paths"][URL]["post"]["requestBody"]["content"]
    )
    assert schema["components"]["schemas"]["AnalysisResponse"]["properties"][
        "analysis"
    ]["anyOf"][0] == {"$ref": "#/components/schemas/AnalysisResult"}
    assert api_case.client.get("/health").json() == {"status": "ok"}
