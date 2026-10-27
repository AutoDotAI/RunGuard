# Local recovery; public deployment deferred

RunGuard currently has no public host, domain, deployment workflow, or
production rollback target. [Issue #34](https://github.com/AutoDotAI/RunGuard/issues/34)
tracks that deferred work. The old deployment scripts and production Compose
override were retired for this local-only milestone.

Use [local recovery](local-recovery.md) for the preserved SQLite backup,
one-time import into PostgreSQL, and `pg_dump`/`pg_restore` commands. Restoring
the old SQLite snapshot after PostgreSQL writes loses those newer writes.
