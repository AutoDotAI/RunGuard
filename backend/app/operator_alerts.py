"""Send only aggregate, fixed-label alert state to local Alertmanager."""

import json
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.request import Request, urlopen

from .alerting import KINDS, stamp


def _send(url: str, body: bytes) -> None:
    request = Request(f"{url.rstrip('/')}/api/v2/alerts", data=body,
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=2) as response:
        if response.status not in (200, 202):
            raise RuntimeError("Local Alertmanager rejected alert state")


class OperatorPublisher:
    def __init__(self, url: str, transport: Callable[[str, bytes], None] = _send,
                 clock: Callable[[], datetime] | None = None):
        self.url = url
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.active: set[str] = set()
        self.started: dict[str, datetime] = {}

    def __call__(self, counts: dict[str, int]) -> None:
        current = self.clock().astimezone(timezone.utc)
        payload = []
        next_active = {kind for kind in KINDS if counts.get(kind, 0) > 0}
        for kind in KINDS:
            if kind not in next_active and kind not in self.active:
                continue
            firing = kind in next_active
            if firing and kind not in self.started:
                self.started[kind] = current
            payload.append({
                "labels": {"alertname": "RunGuardUserImpact", "kind": kind,
                           "severity": "critical" if kind == "worker_queue_stalled" else "warning",
                           "service": "runguard"},
                "annotations": {"summary": "RunGuard worker queue stalled" if kind == "worker_queue_stalled"
                                else "RunGuard users experienced repeated failed runs"},
                "startsAt": stamp(self.started.get(kind, current)),
                "endsAt": stamp(current + timedelta(seconds=30)) if firing else stamp(current),
            })
        if payload:
            self.transport(self.url, json.dumps(payload, separators=(",", ":")).encode())
        self.active = next_active
        self.started = {kind: started for kind, started in self.started.items() if kind in next_active}
