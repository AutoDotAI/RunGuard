"""Capability heartbeat reports reachable providers without exposing credentials."""

import subprocess
from types import SimpleNamespace

from app.workers import runner


def test_disabled_model_worker_publishes_only_extracts_and_probes_no_cli(monkeypatch):
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "disabled")
    monkeypatch.setattr(runner.shutil, "which", lambda _name: (_ for _ in ()).throw(
        AssertionError("disabled worker must not probe CLIs")))
    assert runner._worker_capabilities() == {"providers": ["extractive"]}


def test_capabilities_need_usable_cli_or_server_key_and_publish_no_secrets(monkeypatch):
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "codex")
    monkeypatch.setenv("RUNGUARD_LITELLM_MODELS", "openai/test-model")
    monkeypatch.setenv("OPENAI_API_KEY", "private-test-key")
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/binary/{name}")
    seen = []

    def status(command, **kwargs):
        seen.append((command, kwargs))
        if command[0] == "claude":
            raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, "run", status)
    result = runner._worker_capabilities()
    assert result == {"providers": ["extractive", "codex", "litellm"]}
    assert len(seen) == 2
    assert all(kwargs["timeout"] == runner.CLI_STATUS_TIMEOUT_SECONDS for _, kwargs in seen)
    assert all("OPENAI_API_KEY" not in kwargs["env"] for _, kwargs in seen)
    assert "private-test-key" not in repr(result)


def test_slow_claude_login_status_is_available_without_model_call(monkeypatch):
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "claude")
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/binary/{name}")
    seen = []

    def status(command, **kwargs):
        seen.append((command, kwargs))
        if command[0] == "claude" and kwargs["timeout"] < 5.3:
            raise subprocess.TimeoutExpired(command, timeout=kwargs["timeout"])
        return SimpleNamespace(returncode=0 if command[0] == "claude" else 1)

    monkeypatch.setattr(runner.subprocess, "run", status)
    assert runner._worker_capabilities() == {"providers": ["extractive", "claude"]}
    assert [command for command, _ in seen] == [
        ["codex", "login", "status"], ["claude", "auth", "status"]]
    assert all(kwargs["timeout"] < 20 for _, kwargs in seen)


def test_failed_and_missing_cli_remain_unavailable(monkeypatch):
    monkeypatch.setenv("RUNGUARD_MODEL_PROVIDER", "codex")
    monkeypatch.setattr(runner.shutil, "which", lambda name: None if name == "claude" else "/bin/codex")
    seen = []

    def status(command, **kwargs):
        seen.append(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runner.subprocess, "run", status)
    assert runner._worker_capabilities() == {"providers": ["extractive"]}
    assert seen == [["codex", "login", "status"]]
