# A-share validation runtime: operations

This worktree provides the safe asynchronous boundary for the research and manual-confirmation system. It does not contain broker connectivity and cannot submit, cancel, repair, or automatically recover orders, move funds, or place trades.

## Current scope (2026-09-05)

The current phase is limited to local research validation with SQLite and the repository-owned
fixture. Docker, PostgreSQL 16, Redis/RQ, deployed workers, and production recovery exercises
remain documented future work; they are not acceptance criteria or blockers for this local phase.

## Local run

Run every command below from the repository root with the shared virtual environment activated:

```powershell
.\.venv\Scripts\Activate.ps1
$env:APP_ENV = "development"
$env:AUTH_SECRET_KEY = "development-only-secret-change-me"
$env:DATABASE_URL = "sqlite:///money-mvp.db"
```

In `development`, a fresh local database bootstraps the documented `admin/admin` account with
`USER`, `REVIEWER`, and `ADMIN` roles. This account is local-development-only and is never
created in test, simulation, or production. Set `DEVELOPMENT_USERNAME` and
`DEVELOPMENT_PASSWORD` before starting the app to replace those local credentials.

`tests/fixtures/authorized_simulated_daily_bars.csv` is a repository-owned, explicitly
authorized synthetic test dataset. It is not downloaded data and must not be represented as
real market data. It contains the verification stock bars and `000300.SH` benchmark required
for the local workflow.

After importing that file and creating/publishing a strategy, use the returned owner, batch,
and strategy-version IDs for a daily run. The cutoff must be no later than the batch's data
availability time.

```powershell
$env:DAILY_OWNER_ID = "<owner UUID>"
$env:DAILY_STRATEGY_VERSION_ID = "<published strategy-version UUID>"
$env:DAILY_SOURCE_PATH = (Resolve-Path "tests/fixtures/authorized_simulated_daily_bars.csv").Path
$env:DAILY_INFORMATION_CUTOFF_AT = "<ISO-8601 timestamp with timezone>"
python scripts/run_daily.py --business-date <YYYY-MM-DD>
```

For a persisted batch, set `DAILY_DATA_BATCH_ID` instead of `DAILY_SOURCE_PATH`. The daily
command fails closed when the batch is unavailable, the strategy is not published, or required
configuration is missing. A no-write check is available with `python scripts/run_daily.py --dry-run`.

Run a persisted backtest only after the quality-gated batch and published strategy exist. All
four partition dates must satisfy `start <= train_end < valid_end < oos_start <= end`.

```powershell
$env:BACKTEST_OWNER_ID = "<owner UUID>"
$env:BACKTEST_DATA_BATCH_ID = "<quality-gated batch UUID>"
$env:BACKTEST_STRATEGY_VERSION_ID = "<published strategy-version UUID>"
$env:BACKTEST_START_DATE = "<YYYY-MM-DD>"
$env:BACKTEST_TRAIN_END = "<YYYY-MM-DD>"
$env:BACKTEST_VALID_END = "<YYYY-MM-DD>"
$env:BACKTEST_OOS_START = "<YYYY-MM-DD>"
$env:BACKTEST_END_DATE = "<YYYY-MM-DD>"
$env:BACKTEST_INFORMATION_CUTOFF_AT = "<ISO-8601 timestamp with timezone>"
python scripts/run_backtest.py --business-date <YYYY-MM-DD>
```

Use `python scripts/run_backtest.py --dry-run` to validate the configured backtest without
creating a run. Authorized CSV/Parquet import is explicit; no vendor feed is fetched by the
application. The local SQLite workflow is for development verification only. Production still
requires PostgreSQL 16, Redis/RQ, deployed workers, and genuinely authorized market data;
those external dependencies are not replaced by this fixture.

## Jobs and idempotency

Calendar, data import, quality, daily report, backtest, and backup tasks receive application/domain services by dependency injection. A key is deterministic (`kind:business-date[:scope]`); completed keys replay the recorded value, while failures remain in `job_run` history and may be retried only when the exception is a dependency failure. Data and rule errors are surfaced without automatic retry. Audit events are append-only and contain summaries, never credentials.

## Storage and backup

`ParquetStore` writes immutable `raw`, `standardized`, and `reports` artifacts under content-derived versions and records SHA-256, byte size, row count, and format in `manifest.json`. `scripts/backup.py` copies data and writes a file-level manifest. Run `scripts/restore_check.py path/to/backup-manifest.json --database-url ...` before any human-approved restore; exit code `2` means file hashes are valid but migration/ledger/reproducibility evidence is incomplete. Verification never performs a restore.

## Compose deployment

`docker compose config` validates the file in a current Docker installation. The stack contains app, worker, PostgreSQL 16, and Redis 7. App ports bind to loopback only; no broker network service is present. Set `APP_ENV=development`, `test`, or `simulation` to select separate named data, backup, database, and Redis volumes. Run migrations before publishing, deploy the image, and check app/postgres/redis health. Rollback means stop the new image, restore the previous image tag, and keep the immutable data volumes; never rewrite historical runs. The current environment only exposes the legacy `docker-compose` command, whose static configuration check passes with required temporary values; the requested `docker compose` subcommand is unavailable and the daemon cannot connect, so container startup and PostgreSQL 16/Redis 7/RQ verification remain blocked.

## Recovery and retention

Target RPO is ≤1 hour through hourly database/artifact backups; target RTO is ≤4 hours through a tested image, manifest verification, and documented human restore. Retain job runs, audit events, reports, and referenced data versions for 3 years unless a stricter legal policy applies. Keep backup manifests with their artifacts and test a restore at least quarterly.

P0 is data loss, credential exposure, or an unsafe execution boundary: stop affected services, preserve logs, revoke exposed credentials, and escalate immediately. P1 is a failed daily report, quality gate, or backup: pause publication/manual confirmation, inspect the run and dependency health, then retry the same key after recovery. P2 is a non-blocking report or UI defect: record it, preserve the run, and schedule a normal fix. No incident procedure automatically trades or restores a risk state.

## Verification snapshot (2026-09-04)

The full local suite passes when pytest uses a workspace-owned base directory: `146 passed, 40 warnings` with `F:\PROJECT\Money\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-tmp-final`. The plain command first hit a Windows ACL error while scanning the system pytest temp directory (`114 passed, 31 errors`); this is an environment issue, not a test assertion failure.

Ruff lint and strict mypy pass (`69` app files). Ruff format also passes: `121 files already formatted` after formatting the previously reported 19 files. The persistence-runtime review suite reports `20 passed`. Alembic `upgrade head` and `check` pass on a fresh isolated SQLite verification database through revisions `0001`–`0003`; the existing `data.db` is behind head and was not changed. Static Compose configuration passes with required temporary values through legacy `docker-compose`; the requested `docker compose` subcommand is unavailable and Docker Desktop's daemon is unavailable, so container deployment and PostgreSQL 16/Redis 7/RQ integration remain unverified. The current `.env` has Redis settings but no `DATABASE_URL`; no broker connectivity or order-submit path exists.

The loopback smoke returned `/health` HTTP 200 and `AUTH_REQUIRED` HTTP 401 for unauthenticated data, backtest, and daily-report requests. Readiness timed out when Redis was unavailable, so readiness and container health remain blocked. The only production-tree placeholder hit is the unused `app/api/audit.py`; the mounted audit API is `app/api/v1/admin.py`. `FakeRedis` and `FakeQueue` are test doubles only.

The 200,000-bar performance run recorded `elapsed_seconds=4.842594` and `peak_bytes=242022348` (about 230.8 MiB, tracemalloc). Its input `bars.csv` SHA-256 is `f73ea72f8bcf44dd1472aea4caf7a95647110b7854a608bc68dc0352d6ac4566`, `run_id=run_39b710280504`, and `content_hash=e66de388329e22d4f753d58349bbb50823e59e1162041f43e2fd37aaf069449a`. This records a measured run only; it is not evidence that the five-minute target is met. Isolated SQLite recovery readiness verified the `0001` to `0003` migration chain, two manifest file hashes, and ledger reconciliation, but `run_reproducibility=NOT_CHECKED` and `ready=false`; no real restore overwrite was performed.
