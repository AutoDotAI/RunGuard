"""Pure input rules and fake-DNS/fake-socket URL policy checks."""

import pytest
from pydantic import ValidationError

from app import outbound
from app.models import WorkerCreate, WorkerUpdate
from app.store import Problem, validate_public_url


@pytest.mark.parametrize("payload", [
    {"name": "", "kind": "website_monitor", "url": "https://example.com/"},
    {"name": "  ", "kind": "website_monitor", "url": "https://example.com/"},
    {"name": "x" * 81, "kind": "website_monitor", "url": "https://example.com/"},
    {"name": "Valid", "kind": "unknown", "url": "https://example.com/"},
    {"name": "Valid", "kind": "website_monitor", "url": ""},
    {"name": "Valid", "kind": "website_monitor", "url": "https://example.com/", "config": {"timeout": 1}},
])
def test_invalid_worker_payloads_are_rejected(payload):
    with pytest.raises(ValidationError):
        WorkerCreate.model_validate(payload)


def test_update_rejects_empty_name_and_unknown_config():
    for payload in ({"name": "  "}, {"config": {"secret": "x"}}):
        with pytest.raises(ValidationError):
            WorkerUpdate.model_validate(payload)


@pytest.mark.parametrize("url", [
    "http://example.com/", "https://example.com/article?q=1",
])
def test_public_http_urls_are_accepted_without_dns(url):
    assert validate_public_url(url) == url


@pytest.mark.parametrize("url", [
    "ftp://example.com/", "https://user:password@example.com/", "http://localhost/",
    "http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/",
    "http://192.168.0.1/", "http://[::1]/", "http://example.com/" + "x" * 2048,
])
def test_invalid_or_overlimit_url_never_resolves(url):
    with pytest.raises(Problem) as error:
        validate_public_url(url)
    assert error.value.code == "VALIDATION_ERROR"


@pytest.mark.parametrize("answers,code", [
    (["8.8.8.8", "10.0.0.1"], "DESTINATION_BLOCKED"),
    (["8.8.8.8", "169.254.169.254"], "DESTINATION_BLOCKED"),
    (["127.0.0.1"], "DESTINATION_BLOCKED"),
    (["::1"], "DESTINATION_BLOCKED"),
    (["8.8.8.8", "fd00::1"], "DESTINATION_BLOCKED"),
    (["203.0.113.1"], "DESTINATION_BLOCKED"),
    (["240.0.0.1"], "DESTINATION_BLOCKED"),
    (["8.8.8.8", "203.0.113.1"], "DESTINATION_BLOCKED"),
    ([], "DNS_ERROR"),
])
def test_every_dns_answer_is_checked_before_transport(answers, code):
    def no_connect(*_args):
        pytest.fail("blocked DNS answer reached transport")

    with pytest.raises(outbound.OutboundError) as error:
        outbound.fetch("https://example.com/path", resolver=lambda _host: answers,
                       connection_factory=no_connect)
    assert error.value.code == code


class FakeResponse:
    status = 302

    def getheader(self, _name):
        return None

    def getheaders(self):
        return [("Location", "http://127.0.0.1/private")]

    def read(self, _size):
        return b""


class FakeConnection:
    sock = None

    def __init__(self):
        self.requests = []

    def request(self, method, target, headers):
        self.requests.append((method, target, headers))

    def getresponse(self):
        return FakeResponse()

    def close(self):
        pass


def test_redirect_is_returned_without_following_location():
    connections = []

    def connect(*_args):
        connection = FakeConnection()
        connections.append(connection)
        return connection

    response = outbound.fetch("https://example.com/start", resolver=lambda _host: ["8.8.8.8"],
                              connection_factory=connect)
    assert response.status == 302
    assert len(connections) == 1
    assert [request[1] for request in connections[0].requests] == ["/start"]
    assert response.headers["Location"] == "http://127.0.0.1/private"


def test_oversize_body_stops_at_limit(monkeypatch):
    class LargeResponse(FakeResponse):
        status = 200

        def read(self, _size):
            return b"x" * (outbound.MAX_BODY_BYTES + 1)

    class LargeConnection(FakeConnection):
        def getresponse(self):
            return LargeResponse()

    with pytest.raises(outbound.OutboundError) as error:
        outbound.fetch("https://example.com/", resolver=lambda _host: ["8.8.8.8"],
                       connection_factory=lambda *_args: LargeConnection())
    assert error.value.code == "RESPONSE_TOO_LARGE"
