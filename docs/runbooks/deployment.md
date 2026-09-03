# Deployment runbook

1. Confirm the release tag, migration set, image digest, `APP_ENV`, and a secret supplied outside source control. Do not print secret values.
2. Run migrations using the application migration command and verify the target database health. A migration failure blocks release.
3. Run `docker compose config`, build the image, and start PostgreSQL and Redis first. Confirm their health checks before starting app and worker.
4. Confirm app is reachable only on `127.0.0.1`, `/health` is healthy, RQ can reach Redis, and no container has broker credentials or order-submit routes.
5. Run a dry-run import and daily report, inspect the data version, quality result, run number, and audit summary, then enable normal scheduling.

Rollback: stop app/worker, pin the last known-good image, start it against the same immutable data volumes, and run a read-only health and manifest verification. Do not reset migrations or overwrite artifacts. If a schema rollback is required, use a forward-compatible migration or restore a database backup under change control.

Recovery objectives are RPO ≤1 hour and RTO ≤4 hours. Hourly backups must include PostgreSQL dumps, Parquet artifacts, and manifests; `scripts/restore_check.py` validates a backup before a human-approved restore.
