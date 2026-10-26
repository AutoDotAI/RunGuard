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
# Snapshot the live SQLite database with SQLite's backup API before migrations.
# A failed backup stops the deployment before any running service changes.
volume="$project"_runguard_data
if docker volume inspect "$volume" >/dev/null 2>&1; then
    mkdir -p backups
    chmod 700 backups
    docker run --rm --user 0:0 --network none --read-only \
      -v "$volume:/data:ro" -v "$PWD/backups:/backup" \
      -v "$PWD/deploy/backup.py:/backup.py:ro" "$api_image" \
      python /backup.py --source /data/runguard.db --destination-dir /backup \
      --owner-uid "$(id -u)" --owner-gid "$(id -g)" >/dev/null
fi
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
record() {
    local result=$1
    python3 deploy/state.py deploy --state state --environment "$environment" \
      --result "$result" --commit "$commit" --api "$api_digest" \
      --web "$web_digest" --egress "$egress_digest" \
      --run-id "$run_id" --started-at "$started"
}
if ! "${compose[@]}" up -d --no-build --wait migrate api egress_guard worker web proxy; then
    record failed
    echo 'Deployment failed migration or service health; known-good release records retained' >&2
    exit 1
fi
set -a
# Host-local file is operator-controlled; never print it or the rendered Compose config.
. "./$envfile"
set +a
if ! python3 deploy/smoke.py; then
    record failed
    echo 'Deployment failed health or authenticated smoke; known-good release records retained' >&2
    exit 1
fi
record passed
echo "PASS: $environment deployed commit $commit with recorded digests and authenticated smoke"
