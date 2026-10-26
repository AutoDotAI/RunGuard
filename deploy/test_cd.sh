#!/bin/sh
# Host-free CD validation. No credentials, registry writes, or published ports.
set -eu
repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$repo_dir"
# Pin the only parser dependency; tests include a malformed manual workflow
# and missing SSH credentials, both of which must fail before network access.
uv run --no-project --with pyyaml==6.0.2 python -m unittest discover -s deploy/tests -p 'test_*.py' -v
uv run --no-project --with pyyaml==6.0.2 python deploy/validate_workflows.py .github/workflows/ci.yml .github/workflows/promote.yml
bash -n deploy/ssh_deploy.sh deploy/remote_deploy.sh deploy/remote_rollback.sh deploy/rehearse_rollback.sh
python3 -m py_compile deploy/release.py deploy/state.py deploy/backup.py deploy/check_compatibility.py deploy/smoke.py deploy/validate_workflows.py deploy/compose_overrides.py
envfile=$(mktemp)
config=$(mktemp)
override=$(mktemp)
trap 'rm -f "$envfile" "$config" "$override"' EXIT INT TERM
printf 'RUNGUARD_PUBLIC_HOST=demo.example.org\nRUNGUARD_ALLOWED_HOSTS=example.com\nRUNGUARD_AUTH_SECRET=%s\n' "$(openssl rand -hex 32)" > "$envfile"
export RUNGUARD_API_IMAGE="ghcr.io/autodotai/runguard-api@sha256:$(printf '1%.0s' $(seq 1 64))"
export RUNGUARD_WEB_IMAGE="ghcr.io/autodotai/runguard-web@sha256:$(printf '2%.0s' $(seq 1 64))"
export RUNGUARD_EGRESS_IMAGE="ghcr.io/autodotai/runguard-egress@sha256:$(printf '3%.0s' $(seq 1 64))"
python3 deploy/compose_overrides.py release > "$override"
docker compose --project-name runguard-cd-check --env-file "$envfile" -f compose.prod.yaml -f "$override" config --format json > "$config"
python3 - "$config" <<'PY'
import json, sys
config = json.load(open(sys.argv[1]))
services = config["services"]
for name, image in [("migrate", "api"), ("api", "api"), ("worker", "api"),
                    ("web", "web"), ("egress_guard", "egress")]:
    service = services[name]
    assert "build" not in service, f"{name} still rebuilds source"
    assert service["image"].startswith(f"ghcr.io/autodotai/runguard-{image}@sha256:")
assert services["api"]["volumes"] == services["worker"]["volumes"]
assert config["name"] == "runguard-cd-check"
print("PASS: digest-only Compose, shared SQLite, isolated project")
PY
echo 'PASS: host-free CD validation'
