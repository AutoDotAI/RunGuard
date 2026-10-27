"""The no-key local demo is an extractive worker path, never an AI call."""

import json

import pytest
from fastapi.testclient import TestClient

from app import outbound
from app.db import RunRecord
from app.main import create_app
from app.provider_config import DEMO_LABEL, DEMO_MODEL
from app.workers.runner import WorkerRunner
from app.workers.types import RunSnapshot
from app.workers.url_summarizer import DEMO_MAX_SUMMARY_CHARS, summarize


SECRET = "test-only-signing-secret-32-bytes-minimum"
URL = "https://example.com/demo"
BODY = ("The river rose overnight. Residents moved to higher ground. "
        "Officials monitored the roads. The next update is scheduled tomorrow. " * 3)
DEMO = {"provider_path": "local_demo", "provider": "extractive", "model": DEMO_MODEL}


def page(body: str = BODY, *, content_type: str = "text/html") -> outbound.FetchResponse:
    html = f"<html><title>River report</title><body><script>private-script</script><article>{body}</article></body></html>"
    return outbound.FetchResponse(200, {"Content-Type": content_type}, html.encode())


class NeverModel:
    def complete(self, **_kwargs):
        raise AssertionError("The local demo must never call a model")


def test_demo_extracts_bounded_html_without_model_request_or_untrusted_logs():
    events = []
    snapshot = RunSnapshot("run-id", "url_summarizer", URL, json.dumps(DEMO))
    first = summarize(snapshot, fetch=lambda _url: page(), llm=NeverModel(),
                      log=lambda event, message, level: events.append((event, message, level)))
    second = summarize(snapshot, fetch=lambda _url: page(), llm=NeverModel())
    assert first.error_code is None and first.result == second.result
    assert first.result["model"] == DEMO_LABEL
    assert first.result["summary"].startswith("- The river rose overnight.")
    assert len(first.result["summary"]) <= DEMO_MAX_SUMMARY_CHARS
    assert "private-script" not in json.dumps(first.result) + json.dumps(events)
    assert [event for event, _, _ in events if event.startswith("DEMO_")] == [
        "DEMO_EXTRACTIVE_STARTED", "DEMO_EXTRACTIVE_COMPLETED"]
    assert "LLM_REQUEST_STARTED" not in [event for event, _, _ in events]


@pytest.mark.parametrize("response,code", [
    (outbound.FetchResponse(200, {"Content-Type": "application/pdf"}, b"%PDF-1.7"),
     "UNSUPPORTED_CONTENT_TYPE"),
    (outbound.FetchResponse(200, {"Content-Type": "application/pdf"},
                            b"%PDF-1.7" + b"x" * (outbound.MAX_BODY_BYTES + 1)),
     "RESPONSE_TOO_LARGE"),
])
def test_demo_pdf_fails_before_extractive_summary(response, code):
    events = []
    result = summarize(RunSnapshot("run-id", "url_summarizer", URL, json.dumps(DEMO)),
                       fetch=lambda _url: response, llm=NeverModel(),
                       log=lambda event, message, level: events.append(event))
    assert result.error_code == code and result.result is None
    assert not any(event.startswith("DEMO_") for event in events)


def test_api_queue_worker_demo_result_logs_and_ownership(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'demo.db'}"
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "disabled")
    monkeypatch.setattr(outbound, "fetch", lambda _url: page())
    app = create_app(secret=SECRET, database_url=database_url)
    with TestClient(app) as client:
        def register(email):
            response = client.post("/api/v1/auth/register", json={
                "email": email, "password": "CorrectHorse123!"})
            assert response.status_code == 201
            return {"Authorization": f"Bearer {response.json()['access_token']}"}

        first = register("demo@example.com")
        second = register("other@example.com")
        worker = client.post("/api/v1/workers", headers=first, json={
            "name": "Demo summary", "kind": "url_summarizer", "url": URL})
        assert worker.status_code == 201
        worker_id = worker.json()["id"]
        with WorkerRunner(database_url) as runner:
            # SQLite's legacy worker lock has no capability heartbeat; the
            # real PostgreSQL journey tests the persisted heartbeat separately.
            app.state.ai_settings.capability_reader = lambda: {"extractive"}
            catalog = client.get("/api/v1/ai/providers", headers=first).json()["providers"]
            assert catalog[0] == {"provider_path": "local_demo", "provider": "extractive",
                                  "label": DEMO_LABEL, "available": True,
                                  "unavailable_reason": None,
                                  "models": [{"id": DEMO_MODEL, "label": "Extractive page excerpt"}]}
            assert client.get("/api/v1/ai/settings", headers=first).json() == {**DEMO, "available": True}
            queued = client.post(f"/api/v1/workers/{worker_id}/runs", headers=first)
            assert queued.status_code == 202, queued.text
            run_id = queued.json()["run_id"]
            with app.state.users.sessions() as session:
                assert json.loads(session.get(RunRecord, run_id).config_snapshot_json) == DEMO
            assert runner.run_once() is True
            run = client.get(f"/api/v1/runs/{run_id}", headers=first).json()
            assert run["status"] == "succeeded"
            assert run["result"]["model"] == DEMO_LABEL
            assert "The river rose overnight" in run["result"]["summary"]
            assert client.get(f"/api/v1/runs/{run_id}", headers=second).status_code == 404
            logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=first).json()["items"]
            assert {"DEMO_EXTRACTIVE_STARTED", "DEMO_EXTRACTIVE_COMPLETED"} <= {item["event"] for item in logs}
            assert all("no AI model" in item["message"] for item in logs if item["event"].startswith("DEMO_"))
            assert all("private-script" not in item["message"] for item in logs)
