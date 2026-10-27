"""Selected model is frozen at enqueue and passed to only its chosen adapter."""

import json
from types import SimpleNamespace

import pytest

from app.db import RunRecord
from app.models import WorkerCreate
from app.outbound import FetchResponse
from app.store import Problem
from app.workers.host_model import HostCLIClient
from app.workers.types import RunSnapshot
from app.workers.url_summarizer import summarize


class Settings:
    def __init__(self):
        self.choice = SimpleNamespace(provider_path="backend_cli", provider="codex",
                                      model="default", available=True)
        self.calls = 0

    def get(self, _owner):
        self.calls += 1
        return self.choice


def _config(store, run_id):
    with store.users.sessions() as session:
        return json.loads(session.get(RunRecord, run_id).config_snapshot_json)


def test_summarizer_selection_is_immutable_and_retry_keeps_original(store, owners):
    alice, _ = owners
    settings = Settings()
    store.ai_settings = settings
    worker = store.create_worker(alice, WorkerCreate(
        name="Article", kind="url_summarizer", url="https://example.com/article"))
    source = store.run_worker(alice, worker.id)
    original = _config(store, source.run_id)
    assert original == {"provider_path": "backend_cli", "provider": "codex", "model": "default"}
    settings.choice = SimpleNamespace(provider_path="api", provider="litellm",
                                      model="openai/gpt-4o-mini", available=True)
    with store.users.sessions() as session:
        row = session.get(RunRecord, source.run_id)
        row.status = "failed"
        row.error_code = "LLM_ERROR"
        row.error_message = "Provider failed"
        session.commit()
    retry = store.retry_run(alice, source.run_id)
    assert _config(store, source.run_id) == _config(store, retry.run_id) == original
    assert settings.calls == 1  # Retry never reads later settings.


def test_unavailable_selection_creates_no_run_and_monitor_ignores_settings(store, owners):
    alice, _ = owners
    settings = Settings()
    settings.choice.available = False
    store.ai_settings = settings
    summary = store.create_worker(alice, WorkerCreate(
        name="Article", kind="url_summarizer", url="https://example.com/article"))
    with pytest.raises(Problem) as error:
        store.run_worker(alice, summary.id)
    assert (error.value.status, error.value.code) == (409, "PROVIDER_UNAVAILABLE")
    with store.users.sessions() as session:
        assert session.query(RunRecord).count() == 0
    monitor = store.create_worker(alice, WorkerCreate(
        name="Monitor", kind="website_monitor", url="https://example.com/health"))
    queued = store.run_worker(alice, monitor.id)
    assert _config(store, queued.run_id) == {}
    assert settings.calls == 1


def test_snapshot_selects_exact_litellm_model_without_host_fallback(monkeypatch):
    seen = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setattr("app.workers.url_summarizer.LiteLLMClient.complete",
                        lambda self, **kwargs: seen.append(kwargs) or
                        "# Article\n- Fact one.\n- Fact two.\n- Fact three.")
    monkeypatch.setattr("app.workers.url_summarizer.HostCLIClient.complete",
                        lambda self, **kwargs: pytest.fail("Host fallback"))
    snapshot = RunSnapshot("run-id", "url_summarizer", "https://example.com/article",
                           json.dumps({"provider_path": "api", "provider": "litellm",
                                       "model": "openai/gpt-4o-mini"}))
    body = b"<html><body><article>" + b"This is a readable article with facts and context. " * 5 + b"</article></body></html>"
    result = summarize(snapshot, fetch=lambda _url: FetchResponse(200, {"content-type": "text/html"}, body))
    assert result.error_code is None
    assert result.result["model"] == "openai/gpt-4o-mini"
    assert len(seen) == 1 and seen[0]["model"] == "openai/gpt-4o-mini"


def test_snapshot_selects_exact_host_provider_without_api_fallback(monkeypatch):
    seen = []
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "litellm")
    monkeypatch.setattr("app.workers.url_summarizer.HostCLIClient.complete",
                        lambda self, **kwargs: seen.append((self.provider, kwargs["model"])) or
                        "# Article\n- Fact one.\n- Fact two.\n- Fact three.")
    monkeypatch.setattr("app.workers.url_summarizer.LiteLLMClient.complete",
                        lambda self, **kwargs: pytest.fail("API fallback"))
    snapshot = RunSnapshot("run-id", "url_summarizer", "https://example.com/article",
                           json.dumps({"provider_path": "backend_cli", "provider": "claude",
                                       "model": "claude-sonnet-4-5"}))
    body = b"<html><body><article>" + b"A readable article with facts and context. " * 5 + b"</article></body></html>"
    result = summarize(snapshot, fetch=lambda _url: FetchResponse(200, {"content-type": "text/html"}, body))
    assert result.error_code is None
    assert seen == [("claude", "claude-sonnet-4-5")]


def test_invalid_selected_route_does_not_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "codex")
    monkeypatch.setattr("app.workers.url_summarizer.HostCLIClient.complete",
                        lambda self, **kwargs: pytest.fail("Host fallback"))
    monkeypatch.setattr("app.workers.url_summarizer.LiteLLMClient.complete",
                        lambda self, **kwargs: pytest.fail("API fallback"))
    snapshot = RunSnapshot("run-id", "url_summarizer", "https://example.com/article",
                           json.dumps({"provider_path": "api", "provider": "codex", "model": "default"}))
    body = b"<html><body><article>" + b"A readable article with facts and context. " * 5 + b"</article></body></html>"
    result = summarize(snapshot, fetch=lambda _url: FetchResponse(200, {"content-type": "text/html"}, body))
    assert result.error_code is not None
    assert result.result is None


def test_selected_api_key_removed_after_enqueue_fails_before_model_call(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "codex")
    monkeypatch.setattr("app.workers.url_summarizer.LiteLLMClient.complete",
                        lambda self, **kwargs: pytest.fail("API provider was called"))
    monkeypatch.setattr("app.workers.url_summarizer.HostCLIClient.complete",
                        lambda self, **kwargs: pytest.fail("Host fallback"))
    snapshot = RunSnapshot("run-id", "url_summarizer", "https://example.com/article",
                           json.dumps({"provider_path": "api", "provider": "litellm",
                                       "model": "openai/gpt-4o-mini"}))
    body = b"<article>" + b"A readable recovery report with concrete facts. " * 5 + b"</article>"
    events = []
    result = summarize(snapshot, fetch=lambda _url: FetchResponse(
        200, {"content-type": "text/html"}, body),
        log=lambda event, message, level: events.append((event, message, level)))
    assert result.error_code == "MODEL_PROVIDER_UNAVAILABLE"
    assert result.result is None
    assert "LLM_REQUEST_STARTED" not in [event for event, _, _ in events]


def test_selected_host_cli_removed_after_enqueue_has_distinct_error(monkeypatch):
    from app.workers.host_model import ModelProviderUnavailable

    monkeypatch.setattr("app.workers.url_summarizer.HostCLIClient.complete",
                        lambda self, **kwargs: (_ for _ in ()).throw(
                            ModelProviderUnavailable("test private path")))
    monkeypatch.setattr("app.workers.url_summarizer.LiteLLMClient.complete",
                        lambda self, **kwargs: pytest.fail("API fallback"))
    snapshot = RunSnapshot("run-id", "url_summarizer", "https://example.com/article",
                           json.dumps({"provider_path": "backend_cli", "provider": "codex",
                                       "model": "default"}))
    body = b"<article>" + b"A readable recovery report with concrete facts. " * 5 + b"</article>"
    events = []
    result = summarize(snapshot, fetch=lambda _url: FetchResponse(
        200, {"content-type": "text/html"}, body),
        log=lambda event, message, level: events.append((event, message, level)))
    assert result.error_code == "MODEL_PROVIDER_UNAVAILABLE"
    assert "test private path" not in str(result) + str(events)
    assert "MODEL_PROVIDER_UNAVAILABLE" in [event for event, _, _ in events]


@pytest.mark.parametrize("provider,env,model", [
    ("codex", "RUNGUARD_CODEX_MODELS", "gpt-5-codex"),
    ("claude", "RUNGUARD_CLAUDE_MODELS", "claude-sonnet-4-5"),
])
def test_host_cli_uses_only_configured_model_argument(monkeypatch, provider, env, model):
    from app.workers import host_model

    monkeypatch.setenv(env, model)
    client = HostCLIClient(provider)
    commands = []

    class FakeProcess:
        returncode = 0

    def fake_popen(command, **_kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(host_model.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(host_model, "_read_bounded", lambda _process:
                        b"# Article\n- Fact one.\n- Fact two.\n- Fact three.")
    client.complete(model=model, system="summarize", user="safe article evidence")
    command = commands[0]
    assert command.count("--model") == 1
    assert command[command.index("--model") + 1] == model
    client.complete(model="default", system="summarize", user="safe article evidence")
    assert "--model" not in commands[1]
    with pytest.raises(ValueError, match="allowlisted"):
        client._command("/tmp/model-workdir", "unapproved-model")
    with pytest.raises(ValueError, match="allowlisted"):
        client._command("/tmp/model-workdir", "$(touch /tmp/never)")
