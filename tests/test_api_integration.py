import asyncio
import json
import threading
from uuid import UUID

import pytest

from app.analysis.errors import AnalysisProviderError
from app.persistence.repository import AnalysisJobRepository
from app.storage.local import LocalAudioStorage
from app.transcription.base import TranscriptionProviderError

URL = "/api/v1/analyses"


def test_end_to_end_upload_and_result(api_case, analysis_result):
    case = api_case
    response = case.client.post(URL, files={"file": ("recording.wav", b"audio bytes")})
    assert response.status_code == 202
    assert response.json()["status"] == "PENDING"
    job_id = UUID(response.json()["job_id"])
    result = case.client.get(response.headers["Location"])
    assert result.status_code == 200
    assert result.json() == {
        "job_id": str(job_id),
        "status": "COMPLETED",
        "transcript": "Какая цель?",
        "analysis": analysis_result.model_dump(mode="json"),
        "error": None,
    }
    with case.sessions() as session:
        job = AnalysisJobRepository(session).get_by_id(job_id)
        path = case.storage.get_path(job.audio_path)
        assert path.read_bytes() == b"audio bytes"
    case.transcriber.transcribe.assert_awaited_once_with(path)
    case.analyzer.analyze.assert_awaited_once_with("Какая цель?")


@pytest.mark.parametrize("stage", ["transcription", "analysis"])
def test_background_failure_does_not_break_accepted_response(api_case, caplog, stage):
    case = api_case
    if stage == "transcription":
        case.transcriber.transcribe.side_effect = TranscriptionProviderError(
            "secret audio details"
        )
    else:
        case.analyzer.analyze.side_effect = AnalysisProviderError("secret transcript")
    response = case.client.post(URL, files={"file": ("recording.wav", b"audio")})
    assert response.status_code == 202
    result = case.client.get(response.headers["Location"]).json()
    assert result["status"] == "FAILED"
    assert result["analysis"] is None
    assert result["transcript"] == (None if stage == "transcription" else "Какая цель?")
    assert result["error"] == {"code": f"{stage.upper()}_FAILED", "stage": stage}
    assert "secret" not in response.text + str(result) + caplog.text
    case.transcriber.transcribe.assert_awaited_once()
    if stage == "transcription":
        case.analyzer.analyze.assert_not_awaited()
    else:
        case.analyzer.analyze.assert_awaited_once()


def test_two_uploads_share_provider_instances(api_case):
    for _ in range(2):
        response = api_case.client.post(URL, files={"file": ("a.wav", b"audio")})
        assert response.status_code == 202
        assert (
            api_case.client.get(response.headers["Location"]).json()["status"]
            == "COMPLETED"
        )
    api_case.transcriber_factory.assert_called_once()
    api_case.analyzer_factory.assert_called_once()
    assert api_case.transcriber.transcribe.await_count == 2
    assert api_case.analyzer.analyze.await_count == 2


def test_duplicate_background_execution_claims_once(
    api_case, captured_background, monkeypatch
):
    response = api_case.client.post(URL, files={"file": ("a.wav", b"audio")})
    assert response.status_code == 202
    task = captured_background[0]
    barrier = threading.Barrier(2)
    original = AnalysisJobRepository.claim_pending

    def claim(self, job_id):
        barrier.wait(timeout=5)
        return original(self, job_id)

    monkeypatch.setattr(AnalysisJobRepository, "claim_pending", claim)

    async def run():
        await asyncio.gather(task(), task())

    api_case.client.portal.call(run)
    api_case.transcriber.transcribe.assert_awaited_once()
    api_case.analyzer.analyze.assert_awaited_once()
    assert (
        api_case.client.get(response.headers["Location"]).json()["status"]
        == "COMPLETED"
    )


def test_response_body_sent_before_ai_and_upload_closed(
    api_case, monkeypatch, analysis_result
):
    case = api_case
    messages = []
    streams = []
    ingestion_threads = []
    original = LocalAudioStorage.save

    def save(self, stream, *, filename):
        streams.append(stream)
        ingestion_threads.append(threading.get_ident())
        return original(self, stream, filename=filename)

    monkeypatch.setattr(LocalAudioStorage, "save", save)

    async def transcribe(path):
        # Observe actual ASGI response ordering, without timing TestClient.
        assert any(
            message["type"] == "http.response.start" and message["status"] == 202
            for message in messages
        )
        assert any(
            message["type"] == "http.response.body"
            and not message.get("more_body", False)
            for message in messages
        )
        assert all(stream.closed for stream in streams)
        assert ingestion_threads[0] != threading.get_ident()
        assert case.app.state.analysis_sessions.kw["bind"].pool.checkedout() == 0
        return "Какая цель?"

    case.transcriber.transcribe.side_effect = transcribe

    async def request():
        body = (
            b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
            b"Content-Type: audio/wav\r\n\r\naudio\r\n--boundary--\r\n"
        )
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": URL,
            "raw_path": URL.encode(),
            "root_path": "",
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 123),
            "headers": [(b"content-type", b"multipart/form-data; boundary=boundary")],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await case.app(scope, receive, send)

    case.client.portal.call(request)
    case.transcriber.transcribe.assert_awaited_once()
    data = json.loads(
        next(
            message["body"]
            for message in messages
            if message["type"] == "http.response.body"
        )
    )
    result = case.client.get(f"{URL}/{data['job_id']}").json()
    assert result["status"] == "COMPLETED"
    assert result["analysis"] == analysis_result.model_dump(mode="json")
