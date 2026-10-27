"""Exercise the local Compose API, worker queue, and persistent PostgreSQL volume.

This uses example.com as a public Website Monitor target and needs outbound DNS/HTTPS.
No provider credentials or external authentication service are used.
"""

import json
import secrets
import subprocess
import time
import urllib.request


BASE = "http://127.0.0.1:8000"


def request(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, json.load(response) if response.status != 204 else None


def ready():
    for _ in range(30):
        try:
            if request("GET", "/health")[1] == {"status": "ok"}:
                return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError("API did not become healthy")


def terminal(run_id, token):
    for _ in range(30):
        run = request("GET", f"/api/v1/runs/{run_id}", token=token)[1]
        if run["status"] in ("succeeded", "failed"):
            return run
        time.sleep(1)
    raise RuntimeError(f"Run {run_id} did not finish")


def compose(*args):
    subprocess.run(["docker", "compose", *args], check=True)


def main():
    ready()
    email = f"compose-smoke-{secrets.token_hex(6)}@example.invalid"
    password = "Smoke-" + secrets.token_urlsafe(18)
    status, session = request("POST", "/api/v1/auth/register", {"email": email, "password": password})
    assert status == 201
    token = session["access_token"]
    assert request("GET", "/api/v1/workers", token=token)[1]["total"] == 0
    status, worker = request("POST", "/api/v1/workers", {
        "name": "Compose smoke", "kind": "website_monitor", "url": "https://example.com/"
    }, token)
    assert status == 201
    worker_id = worker["id"]
    status, enqueue = request("POST", f"/api/v1/workers/{worker_id}/runs", token=token)
    assert status == 202 and enqueue["status"] == "queued"
    first_id = enqueue["run_id"]
    first = terminal(first_id, token)
    logs = request("GET", f"/api/v1/runs/{first_id}/logs", token=token)[1]["items"]
    assert logs and all(entry["timestamp"] for entry in logs)
    first_error = first["error"]["code"] if first["error"] else "no error"
    print(f"first run: {first['status']} ({first_error}), {len(logs)} timestamped logs")

    compose("stop", "worker")
    try:
        status, enqueue = request("POST", f"/api/v1/workers/{worker_id}/runs", token=token)
        assert status == 202
        second_id = enqueue["run_id"]
        assert request("GET", f"/api/v1/runs/{second_id}", token=token)[1]["status"] == "queued"
        compose("restart", "api")
        ready()
        assert request("GET", f"/api/v1/runs/{second_id}", token=token)[1]["status"] == "queued"
    finally:
        compose("start", "worker")
    second = terminal(second_id, token)
    second_error = second["error"]["code"] if second["error"] else "no error"
    print(f"queued run after API restart: {second['status']} ({second_error})")

    compose("restart")
    ready()
    status, signed_in = request("POST", "/api/v1/auth/login", {"email": email, "password": password})
    assert status == 200
    token = signed_in["access_token"]
    saved_worker = request("GET", f"/api/v1/workers/{worker_id}", token=token)[1]
    saved_run = request("GET", f"/api/v1/runs/{first_id}", token=token)[1]
    saved_logs = request("GET", f"/api/v1/runs/{first_id}/logs", token=token)[1]["items"]
    assert saved_worker["url"] == "https://example.com/"
    assert saved_run["url"] == "https://example.com/" and saved_logs == logs
    print(f"after full restart: login, worker, run snapshot, {len(saved_logs)} logs persisted")


if __name__ == "__main__":
    main()
