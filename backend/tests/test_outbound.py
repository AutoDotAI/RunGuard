"""Offline tests for the shared SSRF boundary; no live DNS or HTTP calls."""

import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import outbound
from app.main import create_app
from app.store import Problem, validate_public_url
from app.workers.runner import HandlerOutcome, WorkerRunner


@pytest.mark.parametrize("url", [
    "ftp://example.com/", "https://user:secret@example.com/", "http://localhost/",
    "https://foo.local/", "https://metadata.internal/", "https://one-label/",
    "http://127.0.0.1/", "http://127.1/", "http://0x7f000001/",
    "http://169.254.169.254/", "http://10.0.0.1/", "http://172.16.0.1/",
    "http://192.168.1.1/", "http://postgres:5432/",
    "https://[::1]/", "https://[::ffff:127.0.0.1]/",
    "https://example.com:80/", "http://example.com:443/", "https://example.com:9999/",
    "https://example.com:/", "https://-bad.example/", "https://example..com/",
    "https://example.com\\@private.local/", "https://example.com/" + "a" * 2048,
    "https://example.com/\nsecret", "https:///missing-host",
])
def test_same_sanitized_url_policy_for_save_and_execution(url):
    with pytest.raises(Problem) as saved:
        validate_public_url(url)
    with pytest.raises(outbound.OutboundError) as executed:
        outbound.fetch(url, resolver=lambda _host: pytest.fail("Invalid URL caused DNS"))
    assert saved.value.code == "VALIDATION_ERROR"
    assert executed.value.code == "URL_INVALID"
    assert saved.value.message == executed.value.message
    assert url not in executed.value.message


def test_valid_url_rules():
    parsed = outbound.parse_public_url("https://Example.COM:443/path?token=secret#fragment")
    assert parsed.hostname == "example.com"
    assert parsed.port == 443
    assert parsed.target == "/path?token=secret"
    assert validate_public_url(parsed.value) == parsed.value


def test_save_time_policy_never_resolves_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("DNS during save"))
    assert validate_public_url("https://example.com/") == "https://example.com/"


@pytest.mark.parametrize("addresses,code", [
    (["93.184.215.14"], None),
    (["2606:4700:4700::1111"], None),
    (["93.184.215.14", "10.0.0.1"], "DESTINATION_BLOCKED"),
    (["172.19.0.2"], "DESTINATION_BLOCKED"),  # Compose database bridge
    (["192.168.1.1"], "DESTINATION_BLOCKED"),
    (["2606:4700:4700::1111", "fd00::1"], "DESTINATION_BLOCKED"),
    (["169.254.169.254"], "DESTINATION_BLOCKED"),
    (["::ffff:127.0.0.1"], "DESTINATION_BLOCKED"),
    ([], "DNS_ERROR"),
    (["not-an-address"], "DNS_ERROR"),
])
def test_all_dns_answers_checked_before_connect(addresses, code):
    connected = []
    if code:
        with pytest.raises(outbound.OutboundError) as error:
            outbound.fetch("https://example.com/", resolver=lambda _host: addresses,
                           connection_factory=lambda *args: connected.append(args))
        assert error.value.code == code
        assert not connected
    else:
        factory = lambda *args: connected.append(args) or FakeConnection()
        outbound.fetch("https://example.com/", resolver=lambda _host: addresses,
                       connection_factory=factory)
        assert connected[0][2] == addresses[0]


def test_dns_failure_is_sanitized():
    def fail(_host):
        raise OSError("query-token=secret")

    with pytest.raises(outbound.OutboundError) as error:
        outbound.fetch("https://example.com/?query-token=secret", resolver=fail)
    assert error.value.code == "DNS_ERROR"
    assert "secret" not in str(error.value)


def test_dns_lookup_is_wall_clock_bounded_and_stuck_lookup_fails_closed(monkeypatch):
    release = threading.Event()
    monkeypatch.setattr(outbound, "FETCH_TIMEOUT_SECONDS", 0.02)

    def stuck(_host):
        release.wait()
        return ["93.184.215.14"]

    start = time.monotonic()
    try:
        with pytest.raises(outbound.OutboundError) as error:
            outbound.fetch("https://example.com/", resolver=stuck)
        assert error.value.code == "FETCH_TIMEOUT"
        assert time.monotonic() - start < 1
        with pytest.raises(outbound.OutboundError) as retry:
            outbound.fetch("https://example.com/", resolver=stuck)
        assert retry.value.code == "DNS_ERROR"
    finally:
        release.set()
        assert outbound._DNS_SLOT.acquire(timeout=1)
        outbound._DNS_SLOT.release()


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_slow_drip_cannot_extend_total_http_deadline(monkeypatch, phase):
    client, server = socket.socketpair()
    monkeypatch.setattr(outbound, "FETCH_TIMEOUT_SECONDS", 0.08)
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: client)

    def serve():
        try:
            server.recv(4096)
            if phase == "headers":
                payload = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
                for byte in payload:
                    server.sendall(bytes([byte]))
                    time.sleep(0.01)
            else:
                server.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 40\r\n\r\n")
                for _ in range(40):
                    server.sendall(b"x")
                    time.sleep(0.01)
        except OSError:
            pass
        finally:
            server.close()

    peer = threading.Thread(target=serve, daemon=True)
    peer.start()
    start = time.monotonic()
    try:
        with pytest.raises(outbound.OutboundError) as error:
            outbound.fetch("http://example.com/", resolver=lambda _host: ["8.8.8.8"])
        assert error.value.code == "FETCH_TIMEOUT"
        assert time.monotonic() - start < 0.25
        with pytest.raises(outbound.OutboundError) as retry:
            outbound.fetch("http://example.com/", resolver=lambda _host: ["8.8.8.8"])
        assert retry.value.code == "FETCH_ERROR"
    finally:
        peer.join(timeout=2)
        assert outbound._HTTP_SLOT.acquire(timeout=2)
        outbound._HTTP_SLOT.release()


class FakeResponse:
    def __init__(self, status=200, chunks=None, headers=None):
        self.status = status
        self.chunks = list(chunks if chunks is not None else [b"ok"])
        self.headers = headers or []
        self.read_calls = 0

    def getheader(self, key):
        return dict(self.headers).get(key)

    def getheaders(self):
        return self.headers

    def read(self, size):
        self.read_calls += 1
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
            return chunk[:size]
        return chunk


class FakeConnection:
    def __init__(self, response=None, error=None):
        self.response = response or FakeResponse()
        self.error = error
        self.sock = None
        self.timeout = None
        self.requests = []
        self.closed = False

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))
        if self.error:
            raise self.error

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_rebinding_does_not_re_resolve_or_connect_to_later_private_answer():
    calls = []
    connection = FakeConnection()

    def resolver(host):
        calls.append(host)
        return ["93.184.215.14"] if len(calls) == 1 else ["127.0.0.1"]

    result = outbound.fetch("https://example.com/", resolver=resolver,
                            connection_factory=lambda _scheme, _host, address, _port, _timeout:
                            connection if address == "93.184.215.14" else pytest.fail("Rebound address connected"))
    assert result.body == b"ok"
    assert calls == ["example.com"]


def test_pinned_http_socket_and_https_hostname_verification(monkeypatch):
    real_context = outbound.ssl.create_default_context()
    assert real_context.check_hostname
    assert real_context.verify_mode == outbound.ssl.CERT_REQUIRED
    sockets = []

    class Socket:
        def close(self):
            pass

    def connect(destination, timeout):
        sockets.append((destination, timeout))
        return Socket()

    class Context:
        check_hostname = True
        verify_mode = object()

        def wrap_socket(self, raw, *, server_hostname):
            assert server_hostname == "example.com"
            return raw

    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(outbound.ssl, "create_default_context", lambda: Context())
    outbound._PinnedHTTPConnection("example.com", "93.184.215.14", 80, 10).connect()
    outbound._PinnedHTTPSConnection("example.com", "2606:4700:4700::1111", 443, 10).connect()
    assert [destination for destination, _ in sockets] == [
        ("93.184.215.14", 80), ("2606:4700:4700::1111", 443)]


@pytest.mark.parametrize("location", [
    "http://127.0.0.1/private",
    "http://172.19.0.2:5432/",
    "http://169.254.169.254/latest/meta-data/",
])
def test_redirect_not_followed_and_environment_proxy_ignored(monkeypatch, location):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8888")
    response = FakeResponse(302, [b""], [("Location", location)])
    connection = FakeConnection(response)
    calls = []
    result = outbound.fetch("https://example.com/path", resolver=lambda _host: ["93.184.215.14"],
                            connection_factory=lambda *args: calls.append(args) or connection)
    assert result.status == 302
    assert len(calls) == len(connection.requests) == 1
    assert connection.requests[0][1] == "/path"
    assert connection.closed


@pytest.mark.parametrize("response", [
    FakeResponse(headers=[("Content-Length", str(outbound.MAX_BODY_BYTES + 1))]),
    FakeResponse(chunks=[b"x" * 65536] * 17),
])
def test_response_size_is_bounded(response):
    connection = FakeConnection(response)
    with pytest.raises(outbound.OutboundError) as error:
        outbound.fetch("https://example.com/", resolver=lambda _host: ["93.184.215.14"],
                       connection_factory=lambda *args: connection)
    assert error.value.code == "RESPONSE_TOO_LARGE"
    assert connection.closed
    assert response.read_calls <= 17


@pytest.mark.parametrize("failure,code", [
    (TimeoutError("query=secret"), "FETCH_TIMEOUT"),
    (OSError("Authorization: Bearer secret"), "FETCH_ERROR"),
])
def test_transport_failures_are_sanitized(failure, code):
    connection = FakeConnection(error=failure)
    with pytest.raises(outbound.OutboundError) as error:
        outbound.fetch("https://example.com/?query=secret",
                       resolver=lambda _host: ["93.184.215.14"],
                       connection_factory=lambda *args: connection)
    assert error.value.code == code
    assert "secret" not in str(error.value)
    assert connection.timeout <= 10
    assert connection.closed


def test_worker_handler_can_use_shared_fetcher_offline(tmp_path):
    path = tmp_path / "outbound.db"
    client = TestClient(create_app(secret="test-only-signing-secret-32-bytes-minimum",
                                   database_url=f"sqlite:///{path}"))
    with client:
        token = client.post("/api/v1/auth/register", json={
            "email": "alice@example.com", "password": "CorrectHorse123!"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = client.post("/api/v1/workers", headers=headers, json={
            "name": "Check", "kind": "website_monitor", "url": "https://example.com/"})
        assert created.status_code == 201
        enqueued = client.post(f"/api/v1/workers/{created.json()['id']}/runs", headers=headers)
        assert enqueued.status_code == 202

        def handler(snapshot):
            response = outbound.fetch(snapshot.url, resolver=lambda _host: ["93.184.215.14"],
                                      connection_factory=lambda *args: FakeConnection())
            return HandlerOutcome({"http_status": response.status, "response_time_ms": 1,
                                   "healthy": response.status == 200}, response.status)

        with WorkerRunner(f"sqlite:///{path}", handlers={"website_monitor": handler}) as runner:
            assert runner.run_once()
        run = client.get(f"/api/v1/runs/{enqueued.json()['run_id']}", headers=headers).json()
        assert run["status"] == "succeeded"
        assert run["result"]["http_status"] == 200
