# A-share validation runtime: operations

This worktree provides the safe asynchronous boundary for the research and manual-confirmation system. It does not contain broker connectivity and cannot submit, cancel, repair, or automatically recover orders, move funds, or place trades.

## Local run

Use the shared `F:\PROJECT\Money.venv` virtual environment. Set `APP_ENV=development` and a development-only `AUTH_SECRET_KEY`, then preview an operation with `python scripts/run_daily.py --dry-run`. Authorized CSV/JSONL import is explicit: `python scripts/import_data.py path/to/file.csv --dry-run`.

## Jobs and idempotency

Calendar, data import, quality, daily report, backtest, and backup tasks receive application/domain services by dependency injection. A key is deterministic (`kind:business-date[:scope]`); completed keys replay the recorded value, while failures remain in `job_run` history and may be retried only when the exception is a dependency failure. Data and rule errors are surfaced without automatic retry. Audit events are append-only and contain summaries, never credentials.

## Storage and backup

`ParquetStore` writes immutable `raw`, `standardized`, and `reports` artifacts under content-derived versions and records SHA-256, byte size, row count, and format in `manifest.json`. `scripts/backup.py` copies data and writes a file-level manifest. Run `scripts/restore_check.py path/to/backup-manifest.json` before any human-approved restore; verification never performs a restore.

## Compose deployment

`docker compose config` validates the file. The stack contains app, worker, PostgreSQL 16, and Redis 7. App ports bind to loopback only; no broker network service is present. Set `APP_ENV=development`, `test`, or `simulation` to select separate named data, backup, database, and Redis volumes. Run migrations before publishing, deploy the image, and check app/postgres/redis health. Rollback means stop the new image, restore the previous image tag, and keep the immutable data volumes; never rewrite historical runs.

## Recovery and retention

Target RPO is ≤1 hour through hourly database/artifact backups; target RTO is ≤4 hours through a tested image, manifest verification, and documented human restore. Retain job runs, audit events, reports, and referenced data versions for 3 years unless a stricter legal policy applies. Keep backup manifests with their artifacts and test a restore at least quarterly.

P0 is data loss, credential exposure, or an unsafe execution boundary: stop affected services, preserve logs, revoke exposed credentials, and escalate immediately. P1 is a failed daily report, quality gate, or backup: pause publication/manual confirmation, inspect the run and dependency health, then retry the same key after recovery. P2 is a non-blocking report or UI defect: record it, preserve the run, and schedule a normal fix. No incident procedure automatically trades or restores a risk state.
