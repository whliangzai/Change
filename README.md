# A-share validation runtime: operations

This worktree provides the safe asynchronous boundary for the research and manual-confirmation system. It does not contain broker connectivity and cannot submit, cancel, repair, or automatically recover orders, move funds, or place trades.

## Current scope (2026-09-09)

The current phase is still local research validation with SQLite and repository-owned fixtures.
The worktree now also contains recorded/fixture-tested iFinD and Tushare HTTP ingestion paths:
Tushare is the current primary HTTP source, iFinD remains a compatibility path, and AKShare is
validation-only evidence. Real
provider credentials, external PostgreSQL/Redis, deployed workers, full-history backfill and
production recovery exercises remain unverified; they are not replaced by local fixtures.

## Local run

Run every command below from the repository root with the shared virtual environment activated:

```powershell
.\.venv\Scripts\Activate.ps1
$env:APP_ENV = "development"
$env:AUTH_SECRET_KEY = "development-only-secret-change-me"
$env:DATABASE_URL = "sqlite:///money-mvp.db"
```

The application and worker also load an optional `.env` file from the repository root, so these
values do not need to be entered repeatedly in PyCharm. Explicit process environment variables
override `.env`; tests that pass explicit settings remain isolated from the local file. Keep
`.env` uncommitted and do not add trailing semicolons to values.

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
creating a run. Authorized CSV/Parquet import remains supported; provider imports are only
started through the protected ADMIN queue endpoints documented in `docs/runbooks/operations.md`.
Never put a provider token in a task payload or URL. Tushare `full` import stays disabled until
pilot evidence is reviewed and `TUSHARE_FULL_ENABLED=true` is explicitly enabled; iFinD follows
the same gate with `IFIND_FULL_ENABLED=true`. The local
SQLite workflow is for development verification only. Production still requires PostgreSQL 16,
Redis/RQ, deployed workers, and genuinely authorized market data.

## Jobs and idempotency

Calendar, file/provider data import, quality, daily report, backtest, and backup tasks receive
application/domain services by dependency injection. A key is deterministic
(`kind:business-date[:scope]`); provider imports use `data-import:<date>:ifind-<scope>` or
`data-import:<date>:tushare-<scope>`. Provider submissions first persist a `queued` row and the
worker claims it as `running` before calling the provider. The admin APIs expose `QUEUED`,
`RUNNING`, `SUCCEEDED`, and `FAILED`, with polling-safe job IDs and batch/quality summaries.
Completed keys replay the recorded value, while failures remain as append-only `job_run` attempt
history and may be retried only when the exception is a dependency failure. Configure `APP_ENV`,
`DATABASE_URL`, `AUTH_SECRET_KEY`, Redis/RQ, and a running worker explicitly; missing
configuration fails closed and the page reports readiness or queue unavailability without
guessing a substitute provider. Data and rule errors are surfaced without automatic retry.
An active-task partial unique index permits only one `queued`/`running` row for a task key. If a
process dies after the queued row is committed but before Redis enqueue, an ADMIN can recover the
same task after `JOB_QUEUE_STALE_AFTER_SECONDS` (default 300) through the management page; this
keeps the original job ID and task key and records the recovery audit event. Audit events are
append-only and contain summaries, never credentials.

## Storage and backup

`ParquetStore` writes immutable `raw`, `standardized`, and `reports` artifacts under content-derived versions and records SHA-256, byte size, row count, and format in `manifest.json`. `scripts/backup.py` copies data and writes a file-level manifest. Run `scripts/restore_check.py path/to/backup-manifest.json --database-url ...` before any human-approved restore; exit code `2` means file hashes are valid but migration/ledger/reproducibility evidence is incomplete. Verification never performs a restore.

## Compose deployment

`docker compose config` validates the file in a current Docker installation. The stack contains
app, worker, PostgreSQL 16, and Redis 7; the image includes the HTTP client and AKShare
validation dependencies used by provider workers. App ports bind to loopback only; no broker
network service is present. Set `APP_ENV=development`, `test`, or `simulation` to select separate
named data, backup, database, and Redis volumes. Run migrations before publishing, deploy the
image, and check app/postgres/redis health. Rollback means stop the new image, restore the
previous image tag, and keep the immutable data volumes; never rewrite historical runs. The
current environment only exposes the legacy `docker-compose` command, whose static
configuration check passes with required temporary values; the requested `docker compose`
subcommand is unavailable and the daemon cannot connect, so container startup and PostgreSQL
16/Redis 7/RQ verification remain blocked.

## Recovery and retention

Target RPO is ≤1 hour through hourly database/artifact backups; target RTO is ≤4 hours through a tested image, manifest verification, and documented human restore. Retain job runs, audit events, reports, and referenced data versions for 3 years unless a stricter legal policy applies. Keep backup manifests with their artifacts and test a restore at least quarterly.

P0 is data loss, credential exposure, or an unsafe execution boundary: stop affected services, preserve logs, revoke exposed credentials, and escalate immediately. P1 is a failed daily report, quality gate, or backup: pause publication/manual confirmation, inspect the run and dependency health, then retry the same key after recovery. P2 is a non-blocking report or UI defect: record it, preserve the run, and schedule a normal fix. No incident procedure automatically trades or restores a risk state.

## Verification snapshot (2026-09-09)

The full local suite passes when pytest uses a workspace-owned base directory: `238 passed, 67 warnings`. The plain command may hit a Windows ACL error while scanning the system pytest temp directory; that is an environment issue, not a test assertion failure.

Ruff lint and strict mypy pass (`82` app files). Ruff format check currently reports 11 existing/provider-integration files that need formatting; no bulk formatting was applied to the dirty worktree. The persistence-runtime review suite is included in the full test run. Alembic `upgrade head` and `check` pass on a fresh isolated SQLite verification database through revisions `0001`–`0004`; the provider job lifecycle reuses `job_run` and adds no provider-specific table. Static Compose configuration passes with required temporary values through legacy `docker-compose`; the requested `docker compose` subcommand is unavailable and Docker Desktop's daemon is unavailable, so container deployment and PostgreSQL 16/Redis 7/RQ integration remain unverified. The current `.env` has Redis settings but no `DATABASE_URL`; no broker connectivity or order-submit path exists.

The loopback smoke returned `/health` HTTP 200 and `AUTH_REQUIRED` HTTP 401 for unauthenticated data, backtest, and daily-report requests. Readiness timed out when Redis was unavailable, so readiness and container health remain blocked. The only production-tree placeholder hit is the unused `app/api/audit.py`; the mounted audit API is `app/api/v1/admin.py`. `FakeRedis` and `FakeQueue` are test doubles only.

The 200,000-bar performance run recorded `elapsed_seconds=4.842594` and `peak_bytes=242022348` (about 230.8 MiB, tracemalloc). Its input `bars.csv` SHA-256 is `f73ea72f8bcf44dd1472aea4caf7a95647110b7854a608bc68dc0352d6ac4566`, `run_id=run_39b710280504`, and `content_hash=e66de388329e22d4f753d58349bbb50823e59e1162041f43e2fd37aaf069449a`. This records a measured run only; it is not evidence that the five-minute target is met. Isolated SQLite recovery readiness verified the `0001` to `0004` migration chain, two manifest file hashes, and ledger reconciliation, but `run_reproducibility=NOT_CHECKED` and `ready=false`; no real restore overwrite was performed.
