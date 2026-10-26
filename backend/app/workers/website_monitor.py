"""One bounded Website Monitor request through the shared outbound boundary."""

import time
from typing import Callable

from app import outbound

from .types import HandlerOutcome, RunSnapshot


def monitor(snapshot: RunSnapshot, fetch: Callable[[str], outbound.FetchResponse] | None = None
            ) -> HandlerOutcome:
    started = time.monotonic()
    try:
        response = (fetch or outbound.fetch)(snapshot.url)
    except outbound.OutboundError as error:
        return HandlerOutcome(None, error_code=error.code, error_message=error.message)

    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
    healthy = 200 <= response.status < 400
    result = {"http_status": response.status, "response_time_ms": elapsed_ms,
              "healthy": healthy}
    if healthy:
        return HandlerOutcome(result, http_status=response.status)
    if 400 <= response.status < 600:
        return HandlerOutcome(result, http_status=response.status,
                              error_code="HTTP_STATUS_ERROR",
                              error_message=f"Received HTTP {response.status}")
    return HandlerOutcome(None, error_code="FETCH_ERROR",
                          error_message="Destination request failed")
