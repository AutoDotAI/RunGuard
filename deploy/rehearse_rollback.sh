#!/usr/bin/env bash
# Host-free two-image rehearsal. No proxy or published ports; deletes only its own resources.
set -euo pipefail
umask 077
repo_dir=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo_dir"
project="rgrehearse$$"
tag_a="rb$$a"
tag_b="rb$$b"
tempdir=$(mktemp -d)
envfile="$tempdir/.env.development"
state="$tempdir/state"
mkdir -m 700 "$state" "$tempdir/backups"
password=$(openssl rand -hex 20)
email="rehearsal-$$@example.invalid"
printf 'RUNGUARD_PUBLIC_HOST=demo.example.org\nRUNGUARD_ALLOWED_HOSTS=example.com\nRUNGUARD_AUTH_SECRET=%s\n' "$(openssl rand -hex 32)" > "$envfile"
override="$tempdir/rehearsal-compose.yaml"
python3 deploy/compose_overrides.py rehearsal > "$override"
compose=(docker compose --project-name "$project" --env-file "$envfile" -f compose.prod.yaml -f "$override")
cleanup() {
    "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    for kind in api web egress; do
        docker image rm "runguard-$kind:$tag_a" "runguard-$kind:$tag_b" >/dev/null 2>&1 || true
    done
    rm -rf "$tempdir"
}
trap cleanup EXIT
for tag in "$tag_a" "$tag_b"; do
    docker build -q --label "org.runguard.rehearsal=$tag" -t "runguard-api:$tag" backend >/dev/null
    docker build -q --label "org.runguard.rehearsal=$tag" -t "runguard-web:$tag" frontend >/dev/null
    docker build -q --label "org.runguard.rehearsal=$tag" -t "runguard-egress:$tag" deploy/egress >/dev/null
done
image_id() { docker image inspect --format '{{.Id}}' "runguard-$1:$2"; }
record_release() {
    local tag=$1 commit=$2 run_id=$3
    python3 deploy/state.py deploy --state "$state" --environment development --result passed \
      --commit "$commit" --api "$(image_id api "$tag")" \
      --web "$(image_id web "$tag")" --egress "$(image_id egress "$tag")" \
      --run-id "$run_id" --started-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
verify_images() {
    local tag=$1 kind expected actual container
    for kind in api web egress_guard; do
        container=$("${compose[@]}" ps -q "$kind")
        case "$kind" in egress_guard) expected=$(image_id egress "$tag") ;; *) expected=$(image_id "$kind" "$tag") ;; esac
        actual=$(docker inspect --format '{{.Image}}' "$container")
        test "$actual" = "$expected" || { echo "wrong $kind image" >&2; exit 1; }
    done
}
journey() {
    "${compose[@]}" exec -T -e "REHEARSAL_EMAIL=$email" -e "REHEARSAL_PASSWORD=$password" \
      api python - "$1" < deploy/rehearsal_api.py
}
export RUNGUARD_IMAGE_TAG="$tag_a"
"${compose[@]}" config --quiet
"${compose[@]}" up -d --no-build --wait migrate api egress_guard worker web
verify_images "$tag_a"
journey seed
record_release "$tag_a" "$(printf 'a%.0s' {1..40})" 1
export RUNGUARD_IMAGE_TAG="$tag_b"
"${compose[@]}" up -d --no-build --force-recreate --wait migrate api egress_guard worker web
verify_images "$tag_b"
journey verify
record_release "$tag_b" "$(printf 'b%.0s' {1..40})" 2
volume="${project}_runguard_data"
backup=$(docker run --rm --user 0:0 --network none --read-only -v "$volume:/data:ro" \
  -v "$tempdir/backups:/backup" -v "$repo_dir/deploy/backup.py:/backup.py:ro" \
  "runguard-api:$tag_b" python /backup.py --source /data/runguard.db \
  --destination-dir /backup --owner-uid "$(id -u)" --owner-gid "$(id -g)")
backup="$tempdir/backups/$(basename "$backup")"
cp "$backup" "$tempdir/backups/incompatible.db"
BAD_DATABASE="$tempdir/backups/incompatible.db" python3 - <<'PY'
import os, sqlite3
with sqlite3.connect(os.environ['BAD_DATABASE']) as database:
    database.execute("UPDATE alembic_version SET version_num='future-incompatible-revision'")
PY
if docker run --rm --user 0:0 --network none --read-only \
    -v "$tempdir/backups:/backup:ro" \
    -v "$repo_dir/deploy/check_compatibility.py:/check_compatibility.py:ro" \
    "runguard-api:$tag_a" python /check_compatibility.py \
    --database /backup/incompatible.db --environment development \
    --target-commit "$(printf 'a%.0s' {1..40})" >/dev/null 2>&1; then
    echo 'incompatible schema was accepted' >&2
    exit 1
fi
docker run --rm --user 0:0 --network none --read-only \
    -v "$tempdir/backups:/backup:ro" \
    -v "$repo_dir/deploy/check_compatibility.py:/check_compatibility.py:ro" \
    "runguard-api:$tag_a" python /check_compatibility.py \
    --database "/backup/$(basename "$backup")" --environment development \
    --target-commit "$(printf 'a%.0s' {1..40})" \
    > "$state/schema-compatibility.json"
python3 deploy/state.py preflight --state "$state" --environment development \
  --backup "$backup" --approval "$state/schema-compatibility.json" >/dev/null
export RUNGUARD_IMAGE_TAG="$tag_a"
"${compose[@]}" up -d --no-build --force-recreate --wait migrate api egress_guard worker web
verify_images "$tag_a"
journey verify
python3 deploy/state.py rollback-result --state "$state" --environment development --result passed
CURRENT="$state/current.json" PREVIOUS="$state/previous.json" python3 - <<'PY'
import json, os
current = json.load(open(os.environ['CURRENT']))
previous = json.load(open(os.environ['PREVIOUS']))
assert current['commit'] == 'a' * 40
assert previous['commit'] == 'b' * 40
assert current['result'] == previous['result'] == 'passed'
PY
echo 'PASS: two distinct image sets, rollback, health, auth, SQLite worker/run/log persistence'
