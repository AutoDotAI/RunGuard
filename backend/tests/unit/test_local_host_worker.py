"""Host worker obtains the Compose database credential without printing it."""

import json
import subprocess

from sqlalchemy.engine import make_url

from app.local_host_worker import compose_database_url


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
