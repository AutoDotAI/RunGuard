#!/usr/bin/env python3
"""Offline local demo: pause one fixture run, observe alert, resume and resolve."""

import base64
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = os.environ.get("RUNGUARD_DEMO_PROJECT", "")
API = os.environ.get("RUNGUARD_SMOKE_API", "http://127.0.0.1:8000")
GRAFANA = os.environ.get("RUNGUARD_SMOKE_GRAFANA", "http://127.0.0.1:3001")
INBOX = os.environ.get("RUNGUARD_SMOKE_INBOX", "http://127.0.0.1:9094")
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.yaml",
           "-f", "compose.observability.yaml", "-f", "compose.demo.yaml",
           "--profile", "observability"]


def request(url, *, token=None, payload=None, grafana=False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if grafana:
        user = os.environ.get("RUNGUARD_GRAFANA_USER", "admin")
        password = os.environ.get("RUNGUARD_GRAFANA_PASSWORD", "admin")
        headers["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=5) as response:
            content = response.read()
            return response.status, (json.loads(content) if content and response.headers.get("Content-Type", "")
                                     .startswith("application/json") else content.decode())
    except urllib.error.HTTPError as error:
        return error.code, None


def eventually(label, condition, timeout=90):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = condition()
            if last:
                return last
        except (OSError, KeyError, ValueError) as error:
            last = error
        time.sleep(1)
    raise RuntimeError(f"{label} was not observed within {timeout}s: {last}")


def control(command, run_id=None):
    args = [*COMPOSE, "exec", "-T", "worker", "python", "-m", "app.demo_worker", command]
    if run_id:
        args.append(run_id)
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Local demo control {command} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def query(source, path, expression):
    url = f"{GRAFANA}/api/datasources/proxy/uid/{source}/{path}?" + urllib.parse.urlencode({"query": expression})
    status, value = request(url, grafana=True)
    return value if status == 200 else {}


def metric_value(expression):
    rows = query("prometheus", "api/v1/query", expression).get("data", {}).get("result", [])
    return sum(float(row["value"][1]) for row in rows)


def run():
    if os.environ.get("RUNGUARD_DEMO_MODE", "").lower() != "true" or not PROJECT.startswith("rg-demo-"):
        raise SystemExit("Set RUNGUARD_DEMO_MODE=true and RUNGUARD_DEMO_PROJECT=rg-demo-<unique>")
    eventually("local API readiness", lambda: request(f"{API}/health")[0] == 200, timeout=30)
    eventually("local Grafana readiness", lambda: request(f"{GRAFANA}/api/health", grafana=True)[0] == 200,
               timeout=45)
    eventually("local operator inbox readiness", lambda: request(INBOX)[0] == 200, timeout=30)
    marker = "NO_TELEMETRY_28_" + secrets.token_hex(8)
    password = secrets.token_urlsafe(24)
    fixture_email = f"demo-{secrets.token_hex(6)}@example.test"
    other_email = f"other-{secrets.token_hex(6)}@example.test"
    paused = False
    run_id = None
    try:
        state = control("pause")
        paused = True
        assert state == {"status": "paused", "run_id": None, "owner_id": None}
        status, identity = request(f"{API}/api/v1/auth/register", payload={
            "email": fixture_email, "password": password})
        assert status == 201, "Fixture registration failed"
        token = identity["access_token"]
        status, other = request(f"{API}/api/v1/auth/register", payload={
            "email": other_email, "password": password})
        assert status == 201, "Isolation account registration failed"
        other_token = other["access_token"]
        status, worker = request(f"{API}/api/v1/workers", token=token, payload={
            "name": "Local demo: paused worker", "kind": "website_monitor",
            "url": f"https://example.com/demo?private={marker}"})
        assert status == 201
        status, enqueued = request(f"{API}/api/v1/workers/{worker['id']}/runs", token=token, payload={})
        assert status == 202
        run_id = enqueued["run_id"]
        assert control("bind", run_id)["run_id"] == run_id
        assert request(f"{API}/api/v1/runs/{run_id}", token=other_token)[0] == 404
        assert request(f"{API}/api/v1/alerts", token=other_token)[1] == {"items": [], "total": 0}

        eventually("queued depth", lambda: metric_value('runguard_queue_depth{kind="website_monitor"}') >= 1)
        eventually("unhealthy worker heartbeat", lambda: metric_value(
            'runguard_worker_heartbeat_age_seconds{service_name="runguard-api"}') > 15, timeout=30)

        def open_alert():
            status, page = request(f"{API}/api/v1/alerts", token=token)
            if status != 200:
                return None
            rows = [item for item in page["items"] if item["kind"] == "worker_queue_stalled"
                    and item["status"] == "open"]
            return rows[0] if len(rows) == 1 and rows[0]["run_id"] == run_id else None

        alert = eventually("owned stalled alert after >60s", open_alert, timeout=85)
        for _ in range(3):
            assert open_alert()["id"] == alert["id"]
        assert sum(item["kind"] == "worker_queue_stalled" for item in
                   request(f"{API}/api/v1/alerts", token=token)[1]["items"]) == 1
        status, logs = request(f"{API}/api/v1/runs/{run_id}/logs", token=token)
        assert status == 200 and any(item["event"] == "DEMO_WORKER_PAUSED" and item["timestamp"]
                                 for item in logs["items"])
        assert request(f"{API}/api/v1/alerts", token=other_token)[1] == {"items": [], "total": 0}
        eventually("local firing inbox event", lambda: "<td>worker_queue_stalled</td>" in request(INBOX)[1]
                   and "<td>firing</td>" in request(INBOX)[1], timeout=45)
        assert request(INBOX)[1].count("<td>firing</td>") == 1
        eventually("open alert metric", lambda: metric_value(
            'runguard_alerts_open{kind="worker_queue_stalled"}') >= 1)

        assert control("resume")["status"] == "resumed"
        eventually("fixture completed", lambda: request(f"{API}/api/v1/runs/{run_id}", token=token)[1]
                   .get("status") == "succeeded", timeout=30)
        def resolved_alert():
            rows = request(f"{API}/api/v1/alerts", token=token)[1]["items"]
            return next((item for item in rows if item["id"] == alert["id"]
                         and item["status"] == "resolved"), None)
        resolved = eventually("same alert resolved", resolved_alert, timeout=30)
        eventually("local resolved inbox event", lambda: "<td>resolved</td>" in request(INBOX)[1], timeout=65)
        status, logs = request(f"{API}/api/v1/runs/{run_id}/logs", token=token)
        assert status == 200 and {"RUN_QUEUED", "DEMO_WORKER_PAUSED", "DEMO_WORKER_RESUMED",
                                  "RUN_CLAIMED", "RUN_SUCCEEDED"} <= {item["event"] for item in logs["items"]}
        assert all(item["timestamp"] for item in logs["items"])
        trace_id = alert.get("trace_id")
        assert trace_id and len(trace_id) == 32
        def correlated_logs():
            result = query("loki", "loki/api/v1/query_range",
                           f'{{service_name="runguard-worker"}} | run_id="{run_id}"')
            streams = result.get("data", {}).get("result", [])
            return streams if any(stream.get("stream", {}).get("trace_id") == trace_id
                                  for stream in streams) else None
        loki = eventually("correlated local worker log", correlated_logs, timeout=35)
        def trace_ready():
            status, payload = request(f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/traces/{trace_id}",
                                      grafana=True)
            if status != 200:
                return None
            names = {span["name"] for batch in payload.get("batches", [])
                     for group in batch.get("scopeSpans", []) for span in group.get("spans", [])}
            return payload if {"db.enqueue", "worker.claim", "db.result"} <= names else None
        trace = eventually("correlated API and worker trace", trace_ready, timeout=35)
        inbox = request(INBOX)[1]
        captured = json.dumps([alert, resolved, logs, trace, loki, inbox])
        assert not any(secret in captured for secret in (fixture_email, other_email, password,
                                                         token, other_token, marker))
        print(f"PASS LOCAL DEMO: run_id={run_id} alert_id={alert['id']} "
              f"opened_at={alert['created_at']} resolved_at={resolved['resolved_at']} trace_id={trace_id}")
    finally:
        if paused:
            try:
                control("cleanup")
            except Exception as error:
                print(f"FAIL LOCAL DEMO cleanup: {error}")
                raise


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        # Keep failures clear without printing credentials, URLs or page data.
        raise SystemExit(f"FAIL LOCAL DEMO: {type(error).__name__}") from None
