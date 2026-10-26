#!/usr/bin/env bash
# Runs on one preconfigured host; credentials stay in .env.<environment> there.
set -euo pipefail
umask 077

environment=${1:?environment}
commit=${2:?commit}
api_digest=${3:?api digest}
web_digest=${4:?web digest}
egress_digest=${5:?egress digest}
run_id=${6:?actions run id}
case "$environment" in development|production) ;; *) echo 'Invalid environment' >&2; exit 2;; esac
[[ "$run_id" =~ ^[1-9][0-9]*$ ]] || { echo 'Invalid run id' >&2; exit 2; }
python3 deploy/release.py --commit "$commit" --api "$api_digest" --web "$web_digest" --egress "$egress_digest" >/dev/null

envfile=".env.$environment"
test -f "$envfile" || { echo "Missing host-local $envfile" >&2; exit 2; }
test -f .cd-security-approved || { echo 'Host egress/HTTPS gate has not been approved' >&2; exit 2; }
mkdir -p state
api_image="ghcr.io/autodotai/runguard-api@$api_digest"
web_image="ghcr.io/autodotai/runguard-web@$web_digest"
egress_image="ghcr.io/autodotai/runguard-egress@$egress_digest"
export RUNGUARD_API_IMAGE="$api_image" RUNGUARD_WEB_IMAGE="$web_image" RUNGUARD_EGRESS_IMAGE="$egress_image"
project="runguard-$environment"
compose=(docker compose --project-name "$project" --env-file "$envfile" -f compose.prod.yaml -f compose.cd.yaml)

# Read all deployment inputs before changing running containers.
"${compose[@]}" config --quiet
docker pull "$api_image" >/dev/null
docker pull "$web_image" >/dev/null
docker pull "$egress_image" >/dev/null
if test -f state/current.json; then
    cp state/current.json state/previous.json
fi
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
record() {
    local result=$1
    RESULT="$result" ENVIRONMENT="$environment" COMMIT="$commit" API_DIGEST="$api_digest" WEB_DIGEST="$web_digest" EGRESS_DIGEST="$egress_digest" RUN_ID="$run_id" STARTED="$started" python3 - <<'PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path
record = {
    "environment": os.environ["ENVIRONMENT"], "commit": os.environ["COMMIT"],
    "api": os.environ["API_DIGEST"], "web": os.environ["WEB_DIGEST"],
    "egress": os.environ["EGRESS_DIGEST"], "run_id": int(os.environ["RUN_ID"]),
    "started_at": os.environ["STARTED"],
    "finished_at": datetime.now(timezone.utc).isoformat(), "result": os.environ["RESULT"],
}
state = Path("state")
with (state / "attempts.jsonl").open("a") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
if record["result"] == "passed":
    tmp = state / "current.json.tmp"
    tmp.write_text(json.dumps(record, sort_keys=True) + "\n")
    tmp.replace(state / "current.json")
    (state / "latest-success.json").write_text(json.dumps(record, sort_keys=True) + "\n")
else:
    (state / "latest-failure.json").write_text(json.dumps(record, sort_keys=True) + "\n")
PY
}
if ! "${compose[@]}" up -d --no-build --wait migrate api egress_guard worker web proxy; then
    record failed
    echo 'Deployment failed migration or service health; prior digest retained in state/previous.json' >&2
    exit 1
fi
set -a
# Host-local file is operator-controlled; never print it or the rendered Compose config.
. "./$envfile"
set +a
if ! python3 deploy/smoke.py; then
    record failed
    echo 'Deployment failed health or authenticated smoke; prior digest retained in state/previous.json' >&2
    exit 1
fi
record passed
echo "PASS: $environment deployed commit $commit with recorded digests and authenticated smoke"
