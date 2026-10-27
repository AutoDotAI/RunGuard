"""Host worker obtains the Compose database credential without printing it."""

import json
import subprocess

from sqlalchemy.engine import make_url

from app.local_host_worker import compose_database_url, compose_project_name


def test_project_name_uses_compose_config_in_arbitrary_checkout(monkeypatch, tmp_path):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        return subprocess.CompletedProcess(command, 0, json.dumps({"name": "runguard"}), "")

    monkeypatch.setattr("app.local_host_worker.ROOT", tmp_path / "project1")
    monkeypatch.setattr("app.local_host_worker.subprocess.run", fake_run)
    assert compose_project_name() == "runguard"
    assert calls == [(["docker", "compose", "config", "--format", "json"],
                      tmp_path / "project1")]


def test_project_name_honors_compose_environment_override(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        assert kwargs["cwd"] == tmp_path / "project1"
        return subprocess.CompletedProcess(command, 0, json.dumps({"name": "custom"}), "")

    monkeypatch.setattr("app.local_host_worker.ROOT", tmp_path / "project1")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "custom")
    monkeypatch.setattr("app.local_host_worker.subprocess.run", fake_run)
    assert compose_project_name() == "custom"


def test_host_database_url_reads_private_running_compose_credential(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "config" in command:
            return subprocess.CompletedProcess(command, 0, json.dumps({"services": {
                "postgres": {"environment": {"POSTGRES_USER": "owner",
                                             "POSTGRES_DB": "runs"},
                             "ports": [{"published": "49286"}]}}}), "")
        return subprocess.CompletedProcess(command, 0, "private:@password", "")

    monkeypatch.setattr("app.local_host_worker.subprocess.run", fake_run)
    url = make_url(compose_database_url("rg-test"))
    assert url.username == "owner" and url.database == "runs"
    assert url.host == "127.0.0.1" and url.port == 49286
    assert url.password == "private:@password"
    assert all("private:@password" not in " ".join(command) for command in calls)
    assert calls[1][-2:] == ["cat", "/run/runguard-secrets/postgres_password"]
