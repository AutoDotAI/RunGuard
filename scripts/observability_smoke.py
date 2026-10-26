#!/usr/bin/env python3
"""Offline API → fake worker → local collector smoke for an isolated Compose stack.

The script briefly stops the Compose worker to guarantee that its injected
handler, rather than the network-facing production handler, owns the two jobs.
"""

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
API = os.environ.get("RUNGUARD_SMOKE_API", "http://127.0.0.1:8000")
GRAFANA = os.environ.get("RUNGUARD_SMOKE_GRAFANA", "http://127.0.0.1:3001")
PROJECT = os.environ.get("RUNGUARD_COMPOSE_PROJECT", "runguard")
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.yaml",
           "-f", "compose.observability.yaml", "--profile", "observability"]


def request(url: str, *, token: str | None = None, payload: dict | None = None,
            grafana: bool = False, include_request_id: bool = False):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if grafana:
        user = os.environ.get("RUNGUARD_GRAFANA_USER", "admin")
        password = os.environ.get("RUNGUARD_GRAFANA_PASSWORD", "admin")
        basic = base64.b64encode(f"{user}:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {basic}"
    data = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=5) as response:
        result = json.load(response)
        return (result, response.headers.get("X-Request-ID")) if include_request_id else result


def query(source: str, path: str, expression: str) -> dict:
    url = f"{GRAFANA}/api/datasources/proxy/uid/{source}/{path}?" + urllib.parse.urlencode({"query": expression})
    return request(url, grafana=True)


def eventually(label: str, condition, timeout: float = 30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = condition()
            if last:
                return last
        except (urllib.error.URLError, KeyError, ValueError) as error:
            last = error
        time.sleep(1)
    raise RuntimeError(f"{label} not observed within {timeout}s: {last}")


def run() -> None:
    eventually("Grafana ready", lambda: request(f"{GRAFANA}/api/health", grafana=True)
               .get("database") == "ok")
    eventually("RunGuard dashboard", lambda: any(item["uid"] == "runguard-journey" for item in
               request(f"{GRAFANA}/api/search?query=RunGuard", grafana=True)))
    subprocess.run([*COMPOSE, "stop", "worker"], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    try:
        email = f"smoke-{secrets.token_hex(5)}@example.test"
        password = secrets.token_urlsafe(24)
        identity = request(f"{API}/api/v1/auth/register", payload={
            "email": email, "password": password})
        token = identity["access_token"]
        runs = {}
        request_ids = {}
        marker = "DO_NOT_COLLECT_26"
        for outcome in ("ok", "fail", "summary"):
            worker = request(f"{API}/api/v1/workers", token=token, payload={
                "name": f"Telemetry {outcome}",
                "kind": "url_summarizer" if outcome == "summary" else "website_monitor",
                "url": f"https://example.com/{outcome}?secret={marker}"})
            run, request_ids[outcome] = request(
                f"{API}/api/v1/workers/{worker['id']}/runs", token=token,
                payload={}, include_request_id=True)
            runs[outcome] = run["run_id"]
        eventually("queued depth", lambda: sum(float(row["value"][1]) for row in
                   query("prometheus", "api/v1/query", "runguard_queue_depth")
                   .get("data", {}).get("result", [])) >= 3)
        fake_worker = '''from app.workers.runner import WorkerRunner
from app.workers.website_monitor import monitor
from app.workers.url_summarizer import summarize
from app.outbound import FetchResponse
from app import observability
from app.db import RunRecord
from sqlalchemy import select
class FakeModel:
    def complete(self, **_kwargs):
        return "# Offline page\\n- An example page was summarized without a provider key."
def fake(snapshot):
    status = 503 if "/fail?" in snapshot.url else 200
    return monitor(snapshot, fetch=lambda _url: FetchResponse(status, {}, b""))
def fake_summary(snapshot):
    body = b"<html><body><article><p>" + b"An example page for local testing. " * 5 + b"</p></article></body></html>"
    return summarize(snapshot, fetch=lambda _url: FetchResponse(200, {"Content-Type":"text/html"}, body),
                     llm=FakeModel(), model="offline/fake")
with WorkerRunner(handlers={"website_monitor":fake,"url_summarizer":fake_summary}) as runner:
    with runner.db.sessions() as session:
        queued = session.scalars(select(RunRecord).where(RunRecord.status == "queued")
                                 .order_by(RunRecord.created_at, RunRecord.id)).all()
        assert [run.id for run in queued] == EXPECTED, "Use an isolated Compose project with no pre-existing queued jobs"
    assert runner.run_once()
    assert runner.run_once()
    assert runner.run_once()
observability.force_flush()
'''.replace("EXPECTED", repr(list(runs.values())))
        subprocess.run([*COMPOSE, "exec", "-T", "api", "python", "-c", fake_worker],
                       cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        for name, expected in (("ok", "succeeded"), ("fail", "failed"), ("summary", "succeeded")):
            status = request(f"{API}/api/v1/runs/{runs[name]}", token=token)["status"]
            assert status == expected, (name, status)
        for status in ("succeeded", "failed"):
            expression = f'runguard_runs_completed_total{{kind="website_monitor",status="{status}"}}'
            eventually(f"{status} metric", lambda: query("prometheus", "api/v1/query", expression)
                       .get("data", {}).get("result"))
        eventually("summarizer metric", lambda: query("prometheus", "api/v1/query",
                   'runguard_runs_completed_total{kind="url_summarizer",status="succeeded"}')
                   .get("data", {}).get("result"))
        run_id = runs["ok"]
        logs = eventually("Loki run logs", lambda: query(
            "loki", "loki/api/v1/query_range",
            f'{{service_name="runguard-worker"}} | run_id="{run_id}"')
            .get("data", {}).get("result"))
        trace_id = next((stream["stream"].get("trace_id") for stream in logs
                         if stream["stream"].get("trace_id")), None)
        assert trace_id and len(trace_id) == 32
        api_logs = eventually("API request log", lambda: query(
            "loki", "loki/api/v1/query_range",
            f'{{service_name="runguard-api"}} | request_id="{request_ids["ok"]}"')
            .get("data", {}).get("result"))
        assert any(stream["stream"].get("trace_id") == trace_id for stream in api_logs)

        def trace_has_both_services():
            data = request(f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/traces/{trace_id}", grafana=True)
            names = {attribute["value"]["stringValue"] for batch in data.get("batches", [])
                     for attribute in batch.get("resource", {}).get("attributes", [])
                     if attribute["key"] == "service.name"}
            return names if {"runguard-api", "runguard-worker"} <= names else None

        eventually("cross-process trace", trace_has_both_services)
        trace_data = request(f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/traces/{trace_id}", grafana=True)
        summary_logs = eventually("summarizer Loki logs", lambda: query(
            "loki", "loki/api/v1/query_range",
            f'{{service_name="runguard-worker"}} | run_id="{runs["summary"]}"')
            .get("data", {}).get("result"))
        summary_trace_id = next(stream["stream"].get("trace_id") for stream in summary_logs
                                if stream["stream"].get("trace_id"))
        def model_trace():
            data = request(f"{GRAFANA}/api/datasources/proxy/uid/tempo/api/traces/{summary_trace_id}",
                           grafana=True)
            names = {span["name"] for batch in data.get("batches", [])
                     for group in batch.get("scopeSpans", []) for span in group.get("spans", [])}
            return data if {"http.fetch", "llm.summarize", "db.result"} <= names else None

        summary_trace = eventually("model span", model_trace)
        captured = json.dumps([logs, api_logs, trace_data, summary_logs, summary_trace])
        assert all(secret not in captured for secret in (email, password, token, marker))
        print(f"PASS: queued, monitor success/failure, fake-model summary, metrics, Loki logs and cross-process traces for run {run_id}")
    finally:
        subprocess.run([*COMPOSE, "start", "worker"], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    run()
