#!/usr/bin/env bash
# Run the pinned, bounded responder against persisted RunGuard alerts.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
mode=${1:-}
if [[ $# -ne 1 || ( "$mode" != once && "$mode" != watch ) ]]; then
  printf 'Usage: %s once|watch\n' "$0" >&2
  exit 2
fi

interval=${RUNGUARD_RESPONDER_INTERVAL_SECONDS:-15}
if [[ "$mode" == watch ]]; then
  if [[ ! "$interval" =~ ^[0-9]{1,3}$ ]] || (( 10#$interval < 5 || 10#$interval > 300 )); then
    printf 'RUNGUARD_RESPONDER_INTERVAL_SECONDS must be 5–300 seconds\n' >&2
    exit 2
  fi
fi

compose=(docker compose --project-directory "$repo_root"
  -f "$repo_root/compose.yaml" -f "$repo_root/compose.observability.yaml"
  --profile observability --profile responder)

if ! "${compose[@]}" build responder; then
  printf 'Could not build the optional responder image. Check Docker and network access.\n' >&2
  exit 1
fi

child_pid=
stop() {
  trap - INT TERM
  if [[ -n "$child_pid" ]]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" 2>/dev/null || true
  fi
  exit 0
}
trap stop INT TERM

run_once() {
  local status=0
  "${compose[@]}" run --rm --no-deps -T responder &
  child_pid=$!
  if wait "$child_pid"; then
    status=0
  else
    status=$?
  fi
  child_pid=
  return "$status"
}

if [[ "$mode" == once ]]; then
  if run_once; then
    exit 0
  fi
  printf 'Responder scan failed. Open alerts remain stored for the next scan.\n' >&2
  exit 1
fi

printf 'Watching persisted open alerts every %s seconds. Press Ctrl-C to stop.\n' "$interval"
while true; do
  if ! run_once; then
    printf 'Responder scan failed; retrying after %s seconds.\n' "$interval" >&2
  fi
  sleep "$interval" &
  child_pid=$!
  wait "$child_pid" || true
  child_pid=
done
