"""Shared save-time URL policy and pinned, bounded worker-side HTTP fetcher.

Only worker processes call ``fetch``. Callers must not log returned page bodies or
untrusted URLs; public deployment additionally needs network egress controls.
"""

import http.client
import ipaddress
import os
import queue
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urlsplit


MAX_URL_LENGTH = 2048
MAX_BODY_BYTES = 1024 * 1024
FETCH_TIMEOUT_SECONDS = 10.0
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DNS_SLOT = threading.BoundedSemaphore(1)
_HTTP_SLOT = threading.BoundedSemaphore(1)


class OutboundError(Exception):
    """Safe category and message; never contains user URL or response content."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PublicURL:
    value: str
    scheme: str
    hostname: str
    port: int
    target: str
    host_header: str


def parse_public_url(value: str) -> PublicURL:
    """Validate syntax without DNS or network I/O, including at API save time."""
    invalid = OutboundError("URL_INVALID", "A valid public HTTP(S) URL is required")
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise invalid
    if any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        raise invalid
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        username = parsed.username
        password = parsed.password
    except ValueError:
        raise invalid from None
    if parsed.scheme not in {"http", "https"} or not host or username is not None or password is not None:
        raise invalid
    if parsed.netloc.endswith(":") or "%" in parsed.netloc or "@" in parsed.netloc:
        raise invalid
    hostname = host.lower().rstrip(".")
    if (len(hostname) > 253 or "." not in hostname or hostname.endswith((".localhost", ".local", ".internal"))
            or any(not _LABEL.fullmatch(label) for label in hostname.split("."))):
        raise invalid
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise invalid
    # Legacy IPv4 numeric, octal, and hexadecimal spellings can resolve locally.
    try:
        socket.inet_aton(hostname)
    except OSError:
        pass
    else:
        raise invalid
    expected_port = 443 if parsed.scheme == "https" else 80
    if port is not None and port != expected_port:
        raise invalid
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    host_header = hostname if port is None else f"{hostname}:{port}"
    return PublicURL(value, parsed.scheme, hostname, expected_port, target, host_header)


def allowed_public_hosts() -> frozenset[str]:
    """Exact hostnames for the public demo; never accept suffixes or wildcards."""
    raw = os.environ.get("RUNGUARD_ALLOWED_HOSTS", "")
    hosts = [item.strip().lower() for item in raw.split(",") if item.strip()]
    if len(hosts) > 5 or len(hosts) != len(set(hosts)):
        raise ValueError("RUNGUARD_ALLOWED_HOSTS must contain at most five unique hostnames")
    for host in hosts:
        try:
            valid = host == parse_public_url(f"https://{host}/").hostname and ":" not in host and "/" not in host
        except OutboundError:
            valid = False
        if not valid:
            raise ValueError("RUNGUARD_ALLOWED_HOSTS contains an invalid hostname")
    if os.environ.get("RUNGUARD_PUBLIC_MODE") == "1" and not hosts:
        raise ValueError("RUNGUARD_ALLOWED_HOSTS is required in public mode")
    return frozenset(hosts)


def enforce_allowed_host(parsed: PublicURL) -> None:
    hosts = allowed_public_hosts()
    if hosts and parsed.hostname not in hosts:
        raise OutboundError("DESTINATION_BLOCKED", "Destination host is not available in this demo")


def resolve_addresses(hostname: str) -> Sequence[str]:
    """Return all A/AAAA answers. DNS failure is sanitized by the fetcher."""
    return [item[4][0] for item in socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC,
                                                        type=socket.SOCK_STREAM)
            if item[0] in (socket.AF_INET, socket.AF_INET6)]


def _public_addresses(hostname: str, resolver: Callable[[str], Sequence[str]]) -> list[str]:
    try:
        answers = list(resolver(hostname))
    except Exception:
        raise OutboundError("DNS_ERROR", "Destination resolution failed") from None
    if not answers:
        raise OutboundError("DNS_ERROR", "Destination resolution failed")
    validated = []
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer)
        except (TypeError, ValueError):
            raise OutboundError("DNS_ERROR", "Destination resolution failed") from None
        if not address.is_global or (isinstance(address, ipaddress.IPv6Address)
                                     and address.ipv4_mapped is not None
                                     and not address.ipv4_mapped.is_global):
            raise OutboundError("DESTINATION_BLOCKED", "Destination address is not public")
        validated.append(str(address))
    return validated


def _resolve_before_deadline(hostname: str, resolver: Callable[[str], Sequence[str]],
                             deadline: float) -> list[str]:
    """Bound caller wait even if libc DNS blocks; permit only one lingering lookup."""
    if not _DNS_SLOT.acquire(blocking=False):
        raise OutboundError("DNS_ERROR", "Destination resolution unavailable")
    answers: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def lookup() -> None:
        try:
            outcome = (True, _public_addresses(hostname, resolver))
        except Exception as exc:
            outcome = (False, exc)
        finally:
            _DNS_SLOT.release()
        answers.put(outcome)

    threading.Thread(target=lookup, daemon=True, name="runguard-dns").start()
    try:
        ok, result = answers.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        raise OutboundError("FETCH_TIMEOUT", "Destination request timed out") from None
    if not ok:
        if isinstance(result, OutboundError):
            raise result
        raise OutboundError("DNS_ERROR", "Destination resolution failed") from None
    return result


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float):
        super().__init__(hostname, port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._pinned_address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int, timeout: float):
        super().__init__(hostname, port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._pinned_address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _connection(scheme: str, hostname: str, address: str, port: int, timeout: float):
    connection_type = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
    return connection_type(hostname, address, port, timeout)


@dataclass(frozen=True)
class FetchResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def fetch(url: str, *, resolver: Callable[[str], Sequence[str]] = resolve_addresses,
          connection_factory: Callable = _connection) -> FetchResponse:
    """GET once through a pinned socket; no redirects, proxies, or second DNS lookup.

    Resolver and connection factory injection is for offline tests and later handler
    composition. Production callers use the defaults and must add host egress policy.
    """
    deadline = time.monotonic() + FETCH_TIMEOUT_SECONDS
    parsed = parse_public_url(url)
    enforce_allowed_host(parsed)
    addresses = _resolve_before_deadline(parsed.hostname, resolver, deadline)
    # The production firewall permits IPv4 public HTTP(S) only. Prefer an A
    # answer when one is available so dual-stack hosts remain usable there.
    address = next((item for item in addresses if ":" not in item), addresses[0])
    if deadline <= time.monotonic():
        raise OutboundError("FETCH_TIMEOUT", "Destination request timed out")
    # http.client may make many successful recv calls inside getresponse/read;
    # socket timeouts are per recv, so a slow drip can exceed the deadline.
    # One bounded daemon I/O slot guarantees the caller's wall-clock deadline
    # without accumulating unbounded abandoned fetch threads.
    if not _HTTP_SLOT.acquire(blocking=False):
        raise OutboundError("FETCH_ERROR", "Destination request unavailable")
    outcome: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def request_once() -> None:
        try:
            result = (True, _perform_fetch(parsed, address, connection_factory, deadline))
        except Exception as exc:
            result = (False, exc)
        finally:
            _HTTP_SLOT.release()
        outcome.put(result)

    threading.Thread(target=request_once, daemon=True, name="runguard-http").start()
    try:
        ok, result = outcome.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        raise OutboundError("FETCH_TIMEOUT", "Destination request timed out") from None
    if not ok:
        if isinstance(result, OutboundError):
            raise result
        raise OutboundError("FETCH_ERROR", "Destination request failed") from None
    return result


def _perform_fetch(parsed: PublicURL, address: str, connection_factory: Callable,
                   deadline: float) -> FetchResponse:
    connection = None
    try:
        connection = connection_factory(parsed.scheme, parsed.hostname, address,
                                        parsed.port, FETCH_TIMEOUT_SECONDS)

        def remaining() -> float:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError
            connection.timeout = left
            if connection.sock is not None:
                connection.sock.settimeout(left)
            return left

        remaining()
        connection.request("GET", parsed.target, headers={"Host": parsed.host_header,
                                                            "Accept-Encoding": "identity"})
        remaining()
        response = connection.getresponse()
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                if int(length) > MAX_BODY_BYTES:
                    raise OutboundError("RESPONSE_TOO_LARGE", "Response exceeds size limit")
            except ValueError:
                pass
        chunks = []
        size = 0
        while True:
            remaining()
            chunk = response.read(min(64 * 1024, MAX_BODY_BYTES + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_BODY_BYTES:
                raise OutboundError("RESPONSE_TOO_LARGE", "Response exceeds size limit")
            chunks.append(chunk)
        return FetchResponse(response.status, dict(response.getheaders()), b"".join(chunks))
    except OutboundError:
        raise
    except (TimeoutError, socket.timeout):
        raise OutboundError("FETCH_TIMEOUT", "Destination request timed out") from None
    except (OSError, ssl.SSLError, http.client.HTTPException, ValueError):
        raise OutboundError("FETCH_ERROR", "Destination request failed") from None
    finally:
        if connection is not None:
            connection.close()
