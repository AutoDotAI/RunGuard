"""Public-demo policy checks without external DNS, HTTP, or model calls."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.outbound import OutboundError, allowed_public_hosts, fetch
from app.rate_limit import RateLimiter, RateLimited
from app.store import Problem, validate_public_url


SECRET = "public-test-signing-secret-32-bytes-minimum"


def test_public_mode_requires_small_exact_allowlist(monkeypatch):
    monkeypatch.setenv("RUNGUARD_PUBLIC_MODE", "1")
    monkeypatch.delenv("RUNGUARD_ALLOWED_HOSTS", raising=False)
    with pytest.raises(ValueError, match="required"):
        allowed_public_hosts()
    for invalid in ("*.example.com", "example.com:443", "example.com/path", "localhost", "example.com,example.com"):
        monkeypatch.setenv("RUNGUARD_ALLOWED_HOSTS", invalid)
        with pytest.raises(ValueError):
            allowed_public_hosts()
    monkeypatch.setenv("RUNGUARD_ALLOWED_HOSTS", "EXAMPLE.COM,www.python.org")
    assert allowed_public_hosts() == {"example.com", "www.python.org"}


def test_save_and_execution_use_same_exact_host_policy(monkeypatch):
    monkeypatch.setenv("RUNGUARD_PUBLIC_MODE", "1")
    monkeypatch.setenv("RUNGUARD_ALLOWED_HOSTS", "example.com")
    assert validate_public_url("https://EXAMPLE.com/path") == "https://EXAMPLE.com/path"
    for url in ("https://sub.example.com/", "https://example.org/", "http://169.254.169.254/"):
        with pytest.raises(Problem) as save:
            validate_public_url(url)
        assert save.value.status == 422
        with pytest.raises(OutboundError):
            fetch(url, resolver=lambda _: pytest.fail("blocked host reached DNS"))
    with pytest.raises(OutboundError) as rebound:
        fetch("https://example.com/", resolver=lambda _: ["169.254.169.254"])
    assert rebound.value.code == "DESTINATION_BLOCKED"
    for reserved in ("192.88.99.1", "233.1.1.1",
                     "::ffff:192.88.99.1", "::ffff:233.1.1.1"):
        with pytest.raises(OutboundError) as blocked:
            fetch("https://example.com/", resolver=lambda _, address=reserved: [address])
        assert blocked.value.code == "DESTINATION_BLOCKED"


def test_public_startup_requires_allowlist_and_rate_limits_are_observable(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_PUBLIC_MODE", "1")
    monkeypatch.delenv("RUNGUARD_ALLOWED_HOSTS", raising=False)
    app = create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'public.db'}")
    with pytest.raises(ValueError, match="required"), TestClient(app):
        pass
    monkeypatch.setenv("RUNGUARD_ALLOWED_HOSTS", "example.com")
    with TestClient(create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'public.db'}")) as client:
        for index in range(5):
            response = client.post("/api/v1/auth/register", json={
                "email": f"user{index}@example.com", "password": "CorrectHorse123!"})
            assert response.status_code == 201
        blocked = client.post("/api/v1/auth/register", json={
            "email": "user5@example.com", "password": "CorrectHorse123!"})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"
        assert int(blocked.headers["Retry-After"]) > 0
        for _ in range(10):
            assert client.post("/api/v1/auth/login", json={
                "email": "user0@example.com", "password": "WrongPassword123!"}).status_code == 401
        login_blocked = client.post("/api/v1/auth/login", json={
            "email": "user0@example.com", "password": "CorrectHorse123!"})
        assert login_blocked.status_code == 429
        assert int(login_blocked.headers["Retry-After"]) > 0


def test_public_api_rejects_unlisted_worker_and_unavailable_summarizer(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNGUARD_PUBLIC_MODE", "1")
    monkeypatch.setenv("RUNGUARD_ALLOWED_HOSTS", "example.com")
    monkeypatch.delenv("RUNGUARD_SUMMARIZER_AVAILABLE", raising=False)
    with TestClient(create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'public.db'}")) as client:
        token = client.post("/api/v1/auth/register", json={
            "email": "one@example.com", "password": "CorrectHorse123!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        unlisted = client.post("/api/v1/workers", headers=headers, json={
            "name": "Blocked", "kind": "website_monitor", "url": "https://other.example.com/"})
        assert unlisted.status_code == 422
        unavailable = client.post("/api/v1/workers", headers=headers, json={
            "name": "Summary", "kind": "url_summarizer", "url": "https://example.com/"})
        assert unavailable.status_code == 422
        assert unavailable.json()["error"]["code"] == "FEATURE_UNAVAILABLE"
        monkeypatch.setenv("RUNGUARD_SUMMARIZER_AVAILABLE", "true")
        enabled = client.post("/api/v1/workers", headers=headers, json={
            "name": "Summary", "kind": "url_summarizer", "url": "https://example.com/"})
        assert enabled.status_code == 201
        monitor = client.post("/api/v1/workers", headers=headers, json={
            "name": "Allowed", "kind": "website_monitor", "url": "https://example.com/"})
        assert monitor.status_code == 201
        worker_id = monitor.json()["id"]
        for _ in range(10):
            client.post(f"/api/v1/workers/{worker_id}/runs", headers=headers)
        limited = client.post(f"/api/v1/workers/{worker_id}/runs", headers=headers)
        assert limited.status_code == 429
        assert limited.json()["error"]["code"] == "RATE_LIMITED"


def test_rate_limiter_window_and_separate_actions():
    current = [0.0]
    limiter = RateLimiter(enabled=True, clock=lambda: current[0])
    for _ in range(5):
        limiter.enforce("register", "ip-one")
    with pytest.raises(RateLimited) as blocked:
        limiter.enforce("register", "ip-one")
    assert blocked.value.retry_after == 300
    limiter.enforce("register", "ip-two")
    limiter.enforce("login", "ip-one")
    current[0] = 300
    limiter.enforce("register", "ip-one")


def test_health_fails_closed_when_database_unavailable(tmp_path, monkeypatch):
    app = create_app(secret=SECRET, database_url=f"sqlite:///{tmp_path / 'health.db'}")
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

        def disconnected():
            raise OSError("private database location")

        monkeypatch.setattr(app.state.users, "sessions", disconnected)
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["error"] == {
            "code": "NOT_READY", "message": "Database is unavailable"}
        assert "private database location" not in response.text
