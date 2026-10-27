"""Account-owned model calls must not leak or reuse credentials."""

import json

import pytest

from app.outbound import FetchResponse
from app.workers.llm import LiteLLMClient
from app.workers.types import RunSnapshot
from app.workers.url_summarizer import summarize


PAGE = FetchResponse(
    200, {"content-type": "text/html"},
    b"<article>Account-owned summarization needs readable evidence. " * 5 + b"</article>")
SUMMARY = "# Article\n- Fact one.\n- Fact two.\n- Fact three."


def _snapshot(owner: str, model: str) -> RunSnapshot:
    return RunSnapshot("run-1", "url_summarizer", "https://example.com/article",
                       json.dumps({"provider_path": "api", "provider": "litellm",
                                   "model": model}), owner_user_id=owner)


@pytest.mark.parametrize("provider,model", [
    ("openai", "openai/gpt-4.1-mini"),
    ("anthropic", "anthropic/claude-sonnet-4-6"),
])
def test_uses_only_claimed_owners_key(monkeypatch, provider, model):
    seen = []
    key = f"private-{provider}-owner-a"
    monkeypatch.setattr(LiteLLMClient, "complete", lambda _self, **kwargs:
                        seen.append(kwargs) or SUMMARY)

    def lookup(owner, selected_provider):
        assert (owner, selected_provider) == ("owner-a", provider)
        return key

    events = []
    snapshot = _snapshot("owner-a", model)
    result = summarize(snapshot, fetch=lambda _url: PAGE,
                       credential_lookup=lookup,
                       log=lambda event, message, level: events.append((event, message, level)))
    assert result.error_code is None
    assert seen[0]["api_key"] == key
    assert seen[0]["model"] == model
    assert key not in snapshot.config_json
    assert key not in json.dumps(result.result)
    assert key not in json.dumps(events)


@pytest.mark.parametrize("failure", [None, ValueError("private-ciphertext-marker")])
def test_missing_or_unreadable_key_fails_before_model_request(monkeypatch, failure):
    monkeypatch.setattr(LiteLLMClient, "complete", lambda _self, **_kwargs:
                        pytest.fail("A model request was attempted"))
    events = []

    def lookup(_owner, _provider):
        if isinstance(failure, Exception):
            raise failure
        return None

    result = summarize(_snapshot("owner-a", "openai/gpt-4.1-mini"),
                       fetch=lambda _url: PAGE, credential_lookup=lookup,
                       log=lambda event, message, level: events.append((event, message, level)))
    assert result.error_code == "MODEL_PROVIDER_UNAVAILABLE"
    assert "LLM_REQUEST_STARTED" not in [event for event, _, _ in events]
    assert "private-ciphertext-marker" not in str(result) + str(events)


@pytest.mark.parametrize("failure", [RuntimeError("private-api-key-marker"), TimeoutError("private-api-key-marker")])
def test_provider_errors_are_terminal_and_sanitized(monkeypatch, failure):
    monkeypatch.setattr(LiteLLMClient, "complete", lambda _self, **_kwargs: (_ for _ in ()).throw(failure))
    events = []
    result = summarize(_snapshot("owner-a", "anthropic/claude-sonnet-4-6"),
                       fetch=lambda _url: PAGE,
                       credential_lookup=lambda _owner, _provider: "private-api-key-marker",
                       log=lambda event, message, level: events.append((event, message, level)))
    assert result.error_code == "LLM_ERROR"
    assert "private-api-key-marker" not in str(result) + str(events)
    assert [event for event, _, _ in events].count("LLM_REQUEST_STARTED") == 1


def test_litellm_receives_per_call_key_without_environment_change(monkeypatch):
    import litellm

    calls = []
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return type("Response", (), {"choices": [type("Choice", (), {
            "message": type("Message", (), {"content": SUMMARY})()})()]})()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    assert LiteLLMClient().complete(model="openai/gpt-4.1-mini", system="system",
                                    user="page", api_key="private-owner-key") == SUMMARY
    assert calls[0]["api_key"] == "private-owner-key"
    assert calls[0]["num_retries"] == 0
    assert "OPENAI_API_KEY" not in __import__("os").environ
