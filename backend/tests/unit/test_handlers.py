"""Website and summarizer handler decisions with deterministic fake adapters."""

import pytest

from app.outbound import FetchResponse, OutboundError
from app.workers.types import RunSnapshot
from app.workers.url_summarizer import MAX_INPUT_CHARS, summarize
from app.workers.website_monitor import monitor


MONITOR = RunSnapshot("run-1", "website_monitor", "https://example.com/secret-token", "{}")
SUMMARIZER = RunSnapshot("run-2", "url_summarizer", "https://example.com/secret-token", "{}")
READABLE = b"<title>Incident report</title><article><p>" + b"A factual incident report about service recovery. " * 5 + b"</p></article>"


@pytest.mark.parametrize("status,healthy,code", [
    (200, True, None), (302, True, None), (404, False, "HTTP_STATUS_ERROR"),
    (500, False, "HTTP_STATUS_ERROR"),
])
def test_monitor_classifies_status_with_controlled_elapsed(monkeypatch, status, healthy, code):
    ticks = iter([100.0, 100.125])
    monkeypatch.setattr("app.workers.website_monitor.time.monotonic", lambda: next(ticks))
    calls = []

    def fake_fetch(url):
        calls.append(url)
        return FetchResponse(status, {}, b"")

    outcome = monitor(MONITOR, fetch=fake_fetch)
    assert calls == [MONITOR.url]
    assert outcome.http_status == status
    assert outcome.result == {"http_status": status, "response_time_ms": 125,
                              "healthy": healthy}
    assert outcome.error_code == code


@pytest.mark.parametrize("code", ["FETCH_TIMEOUT", "FETCH_ERROR"])
def test_monitor_transport_failure_is_sanitized(code):
    def fake_fetch(_url):
        raise OutboundError(code, "Destination request failed")

    outcome = monitor(MONITOR, fetch=fake_fetch)
    assert outcome.error_code == code
    assert outcome.result is None
    assert "secret-token" not in (outcome.error_message or "")


class FakeLLM:
    def __init__(self, answer="## Overview\n- The page describes a recovery.", error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.answer


def fake_html(body=READABLE, content_type="text/html; charset=utf-8", status=200):
    return lambda _url: FetchResponse(status, {"Content-Type": content_type}, body)


def test_summarizer_extracts_visible_text_and_makes_one_model_call():
    body = (b"<title>Incident report</title><script>script-secret</script>"
            b"<div aria-hidden=' TRUE '>hidden-secret</div><style>style-secret</style>"
            b"<article><p>" + b"The incident response restored service and documented the cause. " * 4
            + b"</p></article>")
    llm = FakeLLM()
    events = []
    outcome = summarize(SUMMARIZER, fetch=fake_html(body), llm=llm,
                        model="fake/model", log=lambda event, message, level:
                        events.append((event, message, level)))
    assert outcome.error_code is None
    assert outcome.result["page_title"] == "Incident report"
    assert outcome.result["summary"] == llm.answer
    assert len(llm.calls) == 1
    prompt = llm.calls[0]["user"]
    assert "restored service" in prompt
    assert not any(secret in prompt for secret in ("script-secret", "hidden-secret", "style-secret"))
    assert "secret-token" not in str(events)


@pytest.mark.parametrize("body,content_type,code", [
    (b"", "text/html", "INSUFFICIENT_CONTENT"),
    (READABLE, "application/json", "UNSUPPORTED_CONTENT_TYPE"),
])
def test_summarizer_rejects_unreadable_page_without_model_call(body, content_type, code):
    llm = FakeLLM()
    outcome = summarize(SUMMARIZER, fetch=fake_html(body, content_type),
                        llm=llm, model="fake/model")
    assert outcome.error_code == code
    assert llm.calls == []


def test_summarizer_truncates_before_single_model_call():
    body = b"<article><p>" + b"Visible article text. " * 1000 + b"</p></article>"
    llm = FakeLLM()
    outcome = summarize(SUMMARIZER, fetch=fake_html(body), llm=llm, model="fake/model")
    assert outcome.error_code is None
    assert outcome.result["input_was_truncated"] is True
    assert len(llm.calls) == 1
    assert len(llm.calls[0]["user"].split(":\n", 1)[1]) <= MAX_INPUT_CHARS


def test_missing_model_stops_before_provider_call():
    llm = FakeLLM()
    outcome = summarize(SUMMARIZER, fetch=fake_html(), llm=llm, model="")
    assert outcome.error_code == "LLM_ERROR"
    assert llm.calls == []


@pytest.mark.parametrize("answer,error", [
    ("", None), (None, RuntimeError("provider-secret and page-secret")),
])
def test_provider_empty_or_error_is_sanitized(answer, error):
    llm = FakeLLM(answer=answer, error=error)
    events = []
    outcome = summarize(SUMMARIZER, fetch=fake_html(), llm=llm, model="fake/model",
                        log=lambda event, message, level: events.append((event, message, level)))
    assert outcome.error_code == "LLM_ERROR"
    assert len(llm.calls) == 1
    assert "secret" not in str(outcome.error_message).lower()
    assert "secret" not in str(events).lower()
