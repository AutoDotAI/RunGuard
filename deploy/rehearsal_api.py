"""Private API journey executed inside the isolated rehearsal API container."""

import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
EMAIL = os.environ["REHEARSAL_EMAIL"]
PASSWORD = os.environ["REHEARSAL_PASSWORD"]


def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(BASE + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.load(response)


credentials = {"email": EMAIL, "password": PASSWORD}
if sys.argv[1] == "seed":
    auth = call("POST", "/api/v1/auth/register", credentials)
    token = auth["access_token"]
    worker = call("POST", "/api/v1/workers", {
        "name": "Rollback persistence probe", "kind": "website_monitor",
        "url": "https://example.com/"}, token)
    run_id = call("POST", f"/api/v1/workers/{worker['id']}/runs", token=token)["run_id"]
    for _ in range(60):
        run = call("GET", f"/api/v1/runs/{run_id}", token=token)
        if run["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.5)
    else:
        raise AssertionError("worker did not finish the queued run")
    logs = call("GET", f"/api/v1/runs/{run_id}/logs", token=token)
    assert logs["items"] and all(item["timestamp"] for item in logs["items"])
    print("PASS: private signup, monitor run, terminal status and timestamped logs")
else:
    token = call("POST", "/api/v1/auth/login", credentials)["access_token"]
    workers = call("GET", "/api/v1/workers", token=token)
    runs = call("GET", "/api/v1/runs", token=token)
    assert workers["total"] == 1 and runs["total"] == 1
    run = runs["items"][0]
    assert run["status"] in ("succeeded", "failed")
    logs = call("GET", f"/api/v1/runs/{run['id']}/logs", token=token)
    assert logs["items"] and all(item["timestamp"] for item in logs["items"])
    print("PASS: private login, worker, run and logs survived image switch")
