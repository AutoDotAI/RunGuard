#!/usr/bin/env python3
"""Offline real-alert -> bounded responder -> demo recovery drill."""

import json
import os
import secrets
import subprocess

from demo_stall_smoke import (API, COMPOSE, INBOX, PROJECT, ROOT, control,
                              eventually, request)


def run():
    if os.environ.get("RUNGUARD_DEMO_MODE", "").lower() != "true" or not PROJECT.startswith("rg-demo-"):
        raise SystemExit("Use RUNGUARD_DEMO_MODE=true and a dedicated rg-demo-* project")
    eventually("API readiness", lambda: request(f"{API}/health")[0] == 200, timeout=30)
    eventually("operator inbox readiness", lambda: request(INBOX)[0] == 200, timeout=45)
    password = secrets.token_urlsafe(24)
    email = f"responder-{secrets.token_hex(6)}@example.test"
    marker = "PRIVATE_29_" + secrets.token_hex(8)
    paused = False
    try:
        control("pause")
        paused = True
        status, account = request(f"{API}/api/v1/auth/register", payload={
            "email": email, "password": password})
        assert status == 201
        token = account["access_token"]
        status, worker = request(f"{API}/api/v1/workers", token=token, payload={
            "name": "Responder demo fixture", "kind": "website_monitor",
            "url": f"https://example.com/demo?private={marker}"})
        assert status == 201
        status, queued = request(f"{API}/api/v1/workers/{worker['id']}/runs", token=token, payload={})
        assert status == 202
        run_id = queued["run_id"]
        control("bind", run_id)
        def open_alert():
            status, page = request(f"{API}/api/v1/alerts", token=token)
            if status != 200:
                return None
            return next((item for item in page["items"] if item["run_id"] == run_id
                         and item["kind"] == "worker_queue_stalled" and item["status"] == "open"), None)
        alert = eventually("real stalled alert", open_alert, timeout=90)
        eventually("firing operator notification", lambda: "<td>worker_queue_stalled</td>" in request(INBOX)[1]
                   and "<td>firing</td>" in request(INBOX)[1], timeout=60)
        command = [*COMPOSE, "--profile", "responder",
                   "run", "--rm", "--no-deps", "responder", "incident-response",
                   "--adapter", "app.responder_adapter:make_adapter", "once"]
        process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=75)
        assert process.returncode == 0, "Responder command failed"
        # The responder may emit structured operational events before its
        # final JSON result; only the last JSON array is the command result.
        incidents = next(json.loads(line) for line in reversed(process.stdout.splitlines())
                         if line.startswith("["))
        incident = next(item for item in incidents if item["alert_id"] == alert["id"])
        assert incident["likely_cause"] == "worker_queue_stalled"
        assert incident["action"]["action"] == "resume_demo_worker"
        assert incident["action"]["result"] == "recovered"
        assert incident["action"]["before_status"] == "queued"
        assert incident["action"]["after_status"] == "succeeded"
        assert incident["evidence"]["inspection_names"] == [
            "get_worker_health", "get_failed_run", "get_recent_errors", "get_queue_depth"]
        eventually("terminal fixture run", lambda: request(f"{API}/api/v1/runs/{run_id}", token=token)[1]
                   .get("status") == "succeeded", timeout=30)
        eventually("resolved owned alert", lambda: any(
            item["id"] == alert["id"] and item["status"] == "resolved" for item in
            request(f"{API}/api/v1/alerts", token=token)[1]["items"]), timeout=30)
        eventually("resolved operator notification", lambda: "<td>resolved</td>" in request(INBOX)[1], timeout=65)
        assert control("status")["status"] == "resumed"
        assert not any(secret in json.dumps(incidents) for secret in (email, password, token, marker))
        print(f"PASS RESPONDER: alert_id={alert['id']} run_id={run_id} "
              f"incident_id={incident['incident_id']} audit_id={incident['action']['id']}")
    finally:
        if paused:
            control("cleanup")


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        raise SystemExit(f"FAIL RESPONDER: {type(error).__name__}") from None
