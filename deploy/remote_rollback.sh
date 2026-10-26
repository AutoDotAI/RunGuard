#!/usr/bin/env bash
# Manual rollback on one already approved host; no caller-supplied image/host.
set -euo pipefail
umask 077
environment=${1:?development or production}
case "$environment" in development|production) ;; *) echo 'Invalid environment' >&2; exit 2;; esac
envfile=".env.$environment"
test -f "$envfile" || { echo "Missing host-local $envfile" >&2; exit 2; }
test -f .cd-security-approved || { echo 'Host egress/HTTPS gate has not been approved' >&2; exit 2; }
mkdir -p state backups
chmod 700 state backups
history=$(python3 deploy/state.py history --state state --environment "$environment")
current_api=$(HISTORY="$history" python3 -c 'import json, os; print(json.loads(os.environ["HISTORY"])["current"]["api"])')
volume="runguard-$environment"_runguard_data
docker volume inspect "$volume" >/dev/null
backup=$(docker run --rm --user 0:0 --network none --read-only \
    -v "$volume:/data:ro" -v "$PWD/backups:/backup" \
    -v "$PWD/deploy/backup.py:/backup.py:ro" \
    "ghcr.io/autodotai/runguard-api@$current_api" \
    python /backup.py --source /data/runguard.db --destination-dir /backup \
    --owner-uid "$(id -u)" --owner-gid "$(id -g)")
backup="$PWD/backups/$(basename "$backup")"
previous_api=$(HISTORY="$history" python3 -c 'import json, os; print(json.loads(os.environ["HISTORY"])["previous"]["api"])')
previous_commit=$(HISTORY="$history" python3 -c 'import json, os; print(json.loads(os.environ["HISTORY"])["previous"]["commit"])')
docker run --rm --user 0:0 --network none --read-only \
    -v "$PWD/backups:/backup:ro" \
    -v "$PWD/deploy/check_compatibility.py:/check_compatibility.py:ro" \
    "ghcr.io/autodotai/runguard-api@$previous_api" \
    python /check_compatibility.py --database "/backup/$(basename "$backup")" \
    --environment "$environment" --target-commit "$previous_commit" \
    > state/schema-compatibility.json
target=$(python3 deploy/state.py preflight --state state --environment "$environment" \
    --backup "$backup" --approval state/schema-compatibility.json)
mapfile -t fields < <(TARGET="$target" python3 - <<'PY'
import json, os
record = json.loads(os.environ['TARGET'])
for key in ('commit', 'api', 'web', 'egress'):
    print(record[key])
PY
)
commit=${fields[0]}
api_image="ghcr.io/autodotai/runguard-api@${fields[1]}"
web_image="ghcr.io/autodotai/runguard-web@${fields[2]}"
egress_image="ghcr.io/autodotai/runguard-egress@${fields[3]}"
export RUNGUARD_API_IMAGE="$api_image" RUNGUARD_WEB_IMAGE="$web_image" RUNGUARD_EGRESS_IMAGE="$egress_image"
compose=(docker compose --project-name "runguard-$environment" --env-file "$envfile" -f compose.prod.yaml -f compose.cd.yaml)
"${compose[@]}" config --quiet
docker pull "$api_image" >/dev/null
docker pull "$web_image" >/dev/null
docker pull "$egress_image" >/dev/null
if ! "${compose[@]}" up -d --no-build --wait migrate api egress_guard worker web proxy; then
    python3 deploy/state.py rollback-result --state state --environment "$environment" --result failed
    echo 'Rollback service health failed; previous successful records retained' >&2
    exit 1
fi
set -a
. "./$envfile"
set +a
if ! python3 deploy/smoke.py; then
    python3 deploy/state.py rollback-result --state state --environment "$environment" --result failed
    echo 'Rollback authenticated smoke failed; previous successful records retained' >&2
    exit 1
fi
python3 deploy/state.py rollback-result --state state --environment "$environment" --result passed
echo "PASS: $environment restored prior successful commit $commit; SQLite volume unchanged"
