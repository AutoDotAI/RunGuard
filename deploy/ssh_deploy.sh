#!/usr/bin/env bash
# GitHub Actions transport. All five release arguments are validated before SSH.
set -euo pipefail
environment=${1:?environment}
commit=${2:?commit}
api_digest=${3:?api digest}
web_digest=${4:?web digest}
egress_digest=${5:?egress digest}
run_id=${6:?run id}
output_path=${7:?evidence output path}
case "$environment" in development|production) ;; *) exit 2;; esac
[[ "$run_id" =~ ^[1-9][0-9]*$ ]] || exit 2
case "$output_path" in development-deployment.json|production-deployment.json) ;; *) exit 2;; esac
python3 deploy/release.py --commit "$commit" --api "$api_digest" --web "$web_digest" --egress "$egress_digest" >/dev/null
: "${CD_SSH_HOST:?Missing environment host}"
: "${CD_SSH_USER:?Missing environment user}"
: "${CD_SSH_KEY:?Missing environment SSH key}"
: "${CD_SSH_KNOWN_HOSTS:?Missing pinned host key}"
tempdir=$(mktemp -d)
trap 'rm -rf "$tempdir"' EXIT
printf '%s\n' "$CD_SSH_KEY" > "$tempdir/key"
printf '%s\n' "$CD_SSH_KNOWN_HOSTS" > "$tempdir/known_hosts"
chmod 600 "$tempdir/key" "$tempdir/known_hosts"
ssh_opts=(-i "$tempdir/key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$tempdir/known_hosts" -o BatchMode=yes)
target="$CD_SSH_USER@$CD_SSH_HOST"
# The host-local .env and release records are intentionally not overwritten.
tar -czf - compose.prod.yaml compose.cd.yaml deploy/Caddyfile deploy/egress deploy/release.py deploy/smoke.py deploy/remote_deploy.sh | \
  ssh "${ssh_opts[@]}" "$target" "mkdir -p \"\$HOME/runguard/$environment\" && tar -xzf - -C \"\$HOME/runguard/$environment\""
ssh "${ssh_opts[@]}" "$target" "cd \"\$HOME/runguard/$environment\" && bash deploy/remote_deploy.sh $environment $commit $api_digest $web_digest $egress_digest $run_id"
ssh "${ssh_opts[@]}" "$target" "cat \"\$HOME/runguard/$environment/state/current.json\"" > "$output_path"
