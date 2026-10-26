# RunGuard deployment, backup, and rollback

Public hosting setup and security gates are in [the deployment README](../deploy/README.md).
Digest publication and manual production promotion are in [CD.md](../deploy/CD.md).
The local A→B→A rehearsal below runs without a host or published ports. The
backup, manual rollback, and restore commands for an actual deployment apply
only to a **single approved host** after its HTTPS and egress QA has passed
under #34. Development and production use separate host directories, Compose
project names, `.env` files, state records, and SQLite volumes.

## Evidence and prerequisites

In `$HOME/runguard/development` or `$HOME/runguard/production`, inspect
`state/current.json`, `state/previous.json`, and `state/attempts.jsonl`.
`current` and `previous` retain the last two **successful** image sets, with
commit, Actions run, three immutable digests, and timestamps. Failed attempts
append to `attempts.jsonl` and update `state/latest-failure.json`; they do not
rotate the known-good records. These files contain no credentials. The host
must retain `.cd-security-approved`, `.env.<environment>`, and GHCR read access.

The previous image must safely read the **current** schema. Before changing
services, rollback snapshots SQLite and runs the previous API image against
that snapshot with networking disabled. The check requires the snapshot's
Alembic revision to equal the previous image's migration head and reads the
user, worker, run, and log mappings. A different revision or query failure
stops the rollback for operator review. Do not force a database downgrade or
claim that a container rollback reverses schema or data changes. Release
migrations should remain backward-compatible for at least one release; test
them against a copy of production-like data before promotion.

## Consistent SQLite backup

`deploy/remote_deploy.sh` creates a private, timestamped SQLite backup before
each upgrade of an existing volume. It uses SQLite's backup API and checks
`PRAGMA integrity_check`, so committed WAL transactions are included. A failed
backup stops the upgrade before services change. `deploy/remote_rollback.sh`
also snapshots the current database before its compatibility check. Find the
private files in `backups/runguard-<UTC timestamp>.db`; keep them outside Git
and CI artifacts and copy them to an encrypted off-host store under your
retention policy. Inspect a snapshot with:

```bash
python3 -c 'import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute("PRAGMA integrity_check").fetchone()[0])' backups/runguard-YYYYMMDDTHHMMSSffffffZ.db
```

For an extra pre-upgrade snapshot on an approved host, from its deployment
directory, substitute its environment:

```bash
environment=development
volume="runguard-${environment}_runguard_data"
api_digest=$(python3 -c 'import json; print(json.load(open("state/current.json"))["api"])')
mkdir -p backups && chmod 700 backups
docker run --rm --user 0:0 --network none --read-only \
  -v "$volume:/data:ro" -v "$PWD/backups:/backup" \
  -v "$PWD/deploy/backup.py:/backup.py:ro" \
  "ghcr.io/autodotai/runguard-api@$api_digest" \
  python /backup.py --source /data/runguard.db --destination-dir /backup \
  --owner-uid "$(id -u)" --owner-gid "$(id -g)"
```

## Manual rollback

On the already approved host, from `$HOME/runguard/development` or
`$HOME/runguard/production`, run the matching command:

```bash
bash deploy/remote_rollback.sh development
# or, from the production directory:
bash deploy/remote_rollback.sh production
```

The command accepts only the environment. It reads that environment's
validated `state/previous.json`; it cannot take an arbitrary host, command,
tag, or digest. It checks the host security marker and local secret file,
backs up SQLite, tests the previous image against the snapshot, validates
digest-only Compose, pulls exactly the recorded API, web, and egress-guard
digests, and reuses the same named SQLite volume. It waits for migration and
service health, then runs `deploy/smoke.py` over HTTPS with a dedicated
authenticated account. A failed health or smoke check returns nonzero and
records a failed attempt; it does **not** mark the rollback successful. On
success `current` and `previous` swap, allowing a controlled return to the
newer release. Inspect the records and the actual user journey afterward.

For a repeatable private two-release development rehearsal from a checkout
with Docker, Python 3, and OpenSSL:

```bash
bash deploy/rehearse_rollback.sh
sh deploy/test_cd.sh
```

The rehearsal builds distinguishable A/B images, starts an isolated Compose
project **without the public proxy or published ports**, creates a user and
worker/run/log data, upgrades to B, snapshots SQLite, validates the old image
against the snapshot, restores A, and checks images, health, login, and saved
data. Its trap removes only its own project, volume, temporary credentials,
and image tags. This local test does not prove rollback on the actual host.

## Recovery when rollback stops

If schema compatibility fails, leave the current release and database in
place. Repair forward or test a data restore in a separate environment;
escalate before touching the live volume. If an explicitly approved recovery
requires restoring a prior backup, stop API and worker first and preserve a
fresh snapshot of the current database. Use the same environment directory
and replace the backup filename below with the chosen verified snapshot:

```bash
environment=development
volume="runguard-${environment}_runguard_data"
api_digest=$(python3 -c 'import json; print(json.load(open("state/current.json"))["api"])')
docker compose --project-name "runguard-$environment" --env-file ".env.$environment" \
  -f compose.prod.yaml stop api worker
docker run --rm --user 0:0 --network none \
  -v "$volume:/data" -v "$PWD/backups:/backup:ro" \
  "ghcr.io/autodotai/runguard-api@$api_digest" python -c \
  'import sqlite3,sys,os; source=sqlite3.connect(sys.argv[1]); target=sqlite3.connect("/data/runguard.db"); source.backup(target); target.close(); source.close(); os.chown("/data/runguard.db",10001,10001)' \
  /backup/runguard-YYYYMMDDTHHMMSSffffffZ.db
```

Check `PRAGMA integrity_check`, then restart only with an image set whose
migrations are compatible with the restored schema and repeat the HTTPS
authenticated smoke. A restore replaces database contents and is a separate,
deliberate recovery action; normal image rollback never does it.

Issue [#25](https://github.com/AutoDotAI/RunGuard/issues/25) covers the
verified local backup and A→B→A rollback rehearsal. Actual development-host
rollback and redacted digest, health, smoke, and persistence evidence are
deferred to [#34](https://github.com/AutoDotAI/RunGuard/issues/34). No host
or domain has been supplied, so this runbook records no public deployment.
