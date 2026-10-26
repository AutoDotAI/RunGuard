#!/bin/sh
# Safe local production-topology probe. Uses its own Compose project/volume.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
project="rgprobe$$"
envfile=$(mktemp)
chmod 600 "$envfile"
cleanup() {
    docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" --profile security-probe down --volumes --remove-orphans >/dev/null 2>&1 || true
    rm -f "$envfile"
}
trap cleanup EXIT INT TERM
printf 'RUNGUARD_PUBLIC_HOST=demo.example.org\nRUNGUARD_ALLOWED_HOSTS=example.com\nRUNGUARD_AUTH_SECRET=%s\n' "$(openssl rand -hex 32)" > "$envfile"

docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" config --quiet
docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" --profile security-probe up --build -d --wait migrate api egress_guard worker web egress_probe

probe_id=$(docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" --profile security-probe ps -q egress_probe)
probe_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$probe_id")
test -n "$probe_ip"
docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" exec -T worker python - "$probe_ip" <<'PY'
import socket
import sys

address = sys.argv[1]
try:
    socket.create_connection((address, 8080), timeout=2).close()
except OSError:
    print("PASS: worker cannot connect to a listening private-address fixture")
else:
    raise SystemExit("FAIL: worker reached a listening private-address fixture")
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        socket.create_connection(listener.getsockname(), timeout=2).close()
    except OSError:
        print("PASS: worker cannot connect to a listening loopback fixture")
    else:
        raise SystemExit("FAIL: worker reached a listening loopback fixture")
PY

for cidr in 10.0.0.0/8 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 203.0.113.0/24; do
    docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" exec -T egress_guard iptables -C OUTPUT -d "$cidr" -j REJECT
done
docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" exec -T egress_guard sh -ec "iptables -S OUTPUT | grep -q -- '-P OUTPUT DROP'; ip6tables -S OUTPUT | grep -q -- '-P OUTPUT DROP'"
docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" exec -T api python - "$project" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

base = "http://127.0.0.1:8000"

def call(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.load(response)

assert call("GET", "/health") == (200, {"status": "ok"})
email = f"probe-{sys.argv[1]}@example.invalid"
_, account = call("POST", "/api/v1/auth/register", {"email": email, "password": "ProbePassword123!"})
token = account["access_token"]
assert call("GET", "/api/v1/workers", token=token)[1]["total"] == 0
try:
    call("POST", "/api/v1/workers", {"name": "Blocked", "kind": "website_monitor",
         "url": "http://169.254.169.254/"}, token)
except urllib.error.HTTPError as error:
    assert error.code == 422
else:
    raise AssertionError("Metadata URL was accepted")
try:
    call("POST", "/api/v1/workers", {"name": "Blocked", "kind": "website_monitor",
         "url": "https://other.example.com/"}, token)
except urllib.error.HTTPError as error:
    assert error.code == 422
else:
    raise AssertionError("Non-allowlisted URL was accepted")
_, worker = call("POST", "/api/v1/workers", {"name": "Probe", "kind": "website_monitor",
                 "url": "https://example.com/"}, token)
_, queued = call("POST", f"/api/v1/workers/{worker['id']}/runs", token=token)
for _ in range(40):
    _, run = call("GET", f"/api/v1/runs/{queued['run_id']}", token=token)
    if run["status"] in ("succeeded", "failed"):
        break
    time.sleep(0.5)
else:
    raise AssertionError("Worker run did not reach a terminal state")
_, logs = call("GET", f"/api/v1/runs/{queued['run_id']}/logs", token=token)
assert logs["items"] and all(entry["timestamp"] for entry in logs["items"])
error = run["error"]["code"] if run["error"] else "none"
print(f"PASS: private API readiness, allowlist, queue, terminal {run['status']} ({error}), timestamped logs")
PY
docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" restart api worker >/dev/null
docker compose --project-name "$project" --env-file "$envfile" -f "$repo_dir/compose.prod.yaml" exec -T api python - "$project" <<'PY'
import json
import sys
import time
import urllib.request

base = "http://127.0.0.1:8000"
for _ in range(30):
    try:
        with urllib.request.urlopen(base + "/health", timeout=2) as response:
            if response.status == 200:
                break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("API did not become healthy after restart")
body = json.dumps({"email": f"probe-{sys.argv[1]}@example.invalid",
                   "password": "ProbePassword123!"}).encode()
request = urllib.request.Request(base + "/api/v1/auth/login", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
with urllib.request.urlopen(request, timeout=5) as response:
    token = json.load(response)["access_token"]
headers = {"Authorization": f"Bearer {token}"}
def get(path):
    with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=5) as response:
        return json.load(response)
workers, runs = get("/api/v1/workers"), get("/api/v1/runs")
assert workers["total"] == 1 and runs["total"] == 1
run = runs["items"][0]
assert run["status"] in ("succeeded", "failed")
assert get(f"/api/v1/runs/{run['id']}/logs")["items"]
print("PASS: account, worker, run, and logs persist after API and worker restart")
PY
echo "PASS: isolated production topology and private egress probe"
