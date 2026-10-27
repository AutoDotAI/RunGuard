"""Offline summarizer behavior and queue-to-API persistence."""

import json

import pytest
from fastapi.testclient import TestClient

from app import outbound
from app.main import create_app
from app.workers.runner import WorkerRunner
from app.workers.llm import LiteLLMClient
from app.workers.types import RunSnapshot
from app.workers.url_summarizer import MAX_INPUT_CHARS, summarize


URL = "https://example.com/article?private=value"
SECRET = "test-only-signing-secret-32-bytes-minimum"
BODY = ("The river flood report describes rising water levels and community response. " * 3)


class FakeLLM:
    def __init__(self, result="# Flood report\n- Water levels rose.\n- Residents responded.\n- Officials monitored roads."):
        self.result = result
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def snapshot():
    return RunSnapshot("run-id", "url_summarizer", URL, "{}")


def page(body=BODY, *, status=200, content_type="text/html; charset=utf-8"):
    html = (f"<html><head><title>Flood &amp; response</title><style>secret style</style></head>"
            f"<body><script>secret script</script><nav>secret navigation</nav>"
            f"<div hidden>secret hidden</div><section aria-hidden=' TRUE '>"
            f"<div><p>secret aria nested</p><span>secret aria payload</span></div></section>"
            f"<aside inert>secret inert</aside><div style='display : none'>secret css</div>"
            f"<article><p>{body}</p></article></body></html>")
    return outbound.FetchResponse(status, {"Content-Type": content_type}, html.encode())


def test_readable_html_title_and_single_neutral_model_call():
    llm = FakeLLM()
    events = []
    result = summarize(snapshot(), fetch=lambda url: page(), llm=llm, model="test/model",
                       log=lambda event, message, level: events.append((event, message, level)))
    assert result.error_code is None
    assert result.result == {"source_url": URL, "page_title": "Flood & response",
                             "summary": llm.result, "model": "test/model",
                             "input_was_truncated": False}
    assert len(llm.calls) == 1
    assert "secret script" not in llm.calls[0]["user"]
    assert "secret style" not in llm.calls[0]["user"]
    assert "secret navigation" not in llm.calls[0]["user"]
    assert "secret hidden" not in llm.calls[0]["user"]
    assert "secret aria nested" not in llm.calls[0]["user"]
    assert "secret aria payload" not in llm.calls[0]["user"]
    assert "secret inert" not in llm.calls[0]["user"]
    assert "secret css" not in llm.calls[0]["user"]
    assert "3 to 6" in llm.calls[0]["system"]
    assert "untrusted" in llm.calls[0]["system"]
    assert "private=value" not in json.dumps(events)


def test_litellm_adapter_disables_sdk_retries(monkeypatch):
    import litellm

    calls = []

    class Message:
        content = "# Summary\n- Fact one.\n- Fact two.\n- Fact three."

    class Choice:
        message = Message()

    class Response:
        choices = [Choice()]

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    result = LiteLLMClient().complete(model="test/model", system="system", user="page")
    assert result == Message.content
    assert len(calls) == 1
    assert calls[0]["num_retries"] == 0
    assert calls[0]["max_tokens"] == 500
    assert calls[0]["timeout"] == 50


def test_charset_and_truncation():
    llm = FakeLLM()
    events = []
    body = "Résumé " + "river update " * 1800
    html = f"<title>Résumé</title><p>{body}</p>".encode("iso-8859-1")
    response = outbound.FetchResponse(200, {"content-type": "text/html; charset=iso-8859-1"}, html)
    result = summarize(snapshot(), fetch=lambda _url: response, llm=llm, model="test/model",
                       log=lambda event, message, level: events.append(event))
    assert result.result["page_title"] == "Résumé"
    assert result.result["input_was_truncated"] is True
    assert len(llm.calls[0]["user"].split("\n", 1)[1]) <= MAX_INPUT_CHARS
    assert "INPUT_TRUNCATED" in events


@pytest.mark.parametrize("response,code", [
    (page("short"), "INSUFFICIENT_CONTENT"),
    (outbound.FetchResponse(200, {"Content-Type": "text/html"}, b""), "INSUFFICIENT_CONTENT"),
    (outbound.FetchResponse(200, {"Content-Type": "application/json"}, b"{}"), "UNSUPPORTED_CONTENT_TYPE"),
    (page(status=503), "HTTP_STATUS_ERROR"),
])
def test_unusable_page_skips_provider(response, code):
    llm = FakeLLM()
    result = summarize(snapshot(), fetch=lambda _url: response, llm=llm, model="test/model")
    assert result.result is None and result.error_code == code
    assert llm.calls == []


@pytest.mark.parametrize("error", [
    outbound.OutboundError("DESTINATION_BLOCKED", "Destination address is not public"),
    outbound.OutboundError("FETCH_TIMEOUT", "Destination request timed out"),
])
def test_fetch_policy_failure_skips_provider(error):
    llm = FakeLLM()

    def blocked(_url):
        raise error

    result = summarize(snapshot(), fetch=blocked, llm=llm, model="test/model")
    assert result.error_code == error.code and not llm.calls


@pytest.mark.parametrize("provider_result", [RuntimeError("private-api-key page body"), "  "])
def test_provider_failure_is_sanitized_without_retry(provider_result):
    llm = FakeLLM(provider_result)
    events = []
    result = summarize(snapshot(), fetch=lambda _url: page(), llm=llm, model="test/model",
                       log=lambda event, message, level: events.append((event, message)))
    assert len(llm.calls) == 1
    assert result.result is None and result.error_code == "LLM_ERROR"
    assert "private-api-key" not in json.dumps(events) + result.error_message


def test_queue_to_persisted_result_and_logs(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'summary.db'}"
    llm = FakeLLM()
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "litellm")
    monkeypatch.setenv("RUNGUARD_LITELLM_MODELS", "openai/test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key-never-sent")
    monkeypatch.setattr(outbound, "fetch", lambda _url: page())
    monkeypatch.setattr(LiteLLMClient, "complete", lambda _self, **kwargs: llm.complete(**kwargs))
    app = create_app(secret=SECRET, database_url=database_url)
    app.state.ai_settings.capability_reader = lambda: {"litellm"}
    with TestClient(app) as client:
        token = client.post("/api/v1/auth/register", json={
            "email": "summary@example.com", "password": "CorrectHorse123!"}).json()["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        selected = client.put("/api/v1/ai/settings", headers=auth, json={
            "provider_path": "api", "provider": "litellm", "model": "openai/test-model"})
        assert selected.status_code == 200, selected.text
        worker = client.post("/api/v1/workers", headers=auth, json={
            "name": "Summary", "kind": "url_summarizer", "url": URL}).json()
        run_id = client.post(f"/api/v1/workers/{worker['id']}/runs", headers=auth).json()["run_id"]
        with WorkerRunner(database_url) as runner:
            assert runner.run_once() is True
            assert runner.run_once() is False
        run = client.get(f"/api/v1/runs/{run_id}", headers=auth).json()
        assert run["status"] == "succeeded"
        assert run["result"]["summary"] == llm.result
        assert run["result"]["source_url"] == URL
        assert run["duration_ms"] >= 0
        logs = client.get(f"/api/v1/runs/{run_id}/logs", headers=auth).json()["items"]
        assert [item["event"] for item in logs][-1] == "RUN_SUCCEEDED"
        assert "LLM_REQUEST_STARTED" in [item["event"] for item in logs]
        assert "LLM_RESPONSE_RECEIVED" in [item["event"] for item in logs]
        assert "private=value" not in json.dumps(logs)
