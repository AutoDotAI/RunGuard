"""Loopback-only operator inbox for aggregate Alertmanager webhook deliveries."""

import html
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .alerting import KINDS


def sanitize_delivery(data: object) -> list[dict[str, str]]:
    if not isinstance(data, dict) or not isinstance(data.get("alerts"), list):
        raise ValueError("Invalid alert delivery")
    safe = []
    for alert in data["alerts"]:
        if not isinstance(alert, dict) or not isinstance(alert.get("labels"), dict):
            raise ValueError("Invalid alert entry")
        labels = alert["labels"]
        kind = labels.get("kind")
        status = alert.get("status")
        severity = labels.get("severity")
        if (set(labels) != {"alertname", "kind", "severity", "service"}
                or labels.get("alertname") != "RunGuardUserImpact"
                or labels.get("service") != "runguard"
                or kind not in KINDS or status not in {"firing", "resolved"}
                or severity != ("critical" if kind == "worker_queue_stalled" else "warning")):
            raise ValueError("Unexpected alert labels")
        safe.append({"received_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                     "kind": kind, "severity": severity, "status": status})
    return safe


class Handler(BaseHTTPRequestHandler):
    path_file = Path(os.environ.get("RUNGUARD_OPERATOR_INBOX_PATH", "/data/operator-alerts.jsonl"))

    def do_POST(self):
        if self.path != "/":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError("Invalid payload length")
            entries = sanitize_delivery(json.loads(self.rfile.read(length)))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        self.path_file.parent.mkdir(parents=True, exist_ok=True)
        with self.path_file.open("a", encoding="utf-8") as stream:
            for entry in entries:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        entries = []
        if self.path_file.exists():
            entries = [json.loads(line) for line in self.path_file.read_text().splitlines()[-100:]]
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(entry[key]))}</td>"
                                      for key in ("received_at", "kind", "severity", "status")) + "</tr>"
                       for entry in reversed(entries))
        page = ("<!doctype html><title>RunGuard operator inbox</title>"
                "<h1>RunGuard operator inbox</h1><p>Aggregate alerts only; no user identifiers.</p>"
                "<table><thead><tr><th>Received</th><th>Kind</th><th>Severity</th><th>Status</th>"
                f"</tr></thead><tbody>{rows}</tbody></table>")
        body = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 9094), Handler).serve_forever()


if __name__ == "__main__":
    main()
