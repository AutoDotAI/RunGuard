"""Values passed from durable queue claims to worker handlers."""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class RunSnapshot:
    id: str
    kind: str
    url: str
    config_json: str
    traceparent: str | None = None

    @property
    def config(self) -> dict:
        """Return a fresh copy; handler edits cannot change the queued snapshot."""
        return json.loads(self.config_json)


@dataclass(frozen=True)
class HandlerOutcome:
    result: dict | None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
