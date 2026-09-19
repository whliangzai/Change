# Operations runbook

## Daily close

1. Confirm the authorized data batch and calendar are available.
2. Run calendar update, import, and quality jobs with the business date. An error-level quality result blocks downstream work.
3. Run the daily signal/report job. Review data date, strategy/cost/rule versions, run number, risk state, and warnings.
4. Treat order plans as review documents only. Human confirmation and actual fills remain separate records; no job submits, cancels, repairs, or automatically recovers an order.
5. Verify the report artifact manifest and record the operator review.

## iFinD HTTP import scheduling

The external scheduler calls `POST /api/v1/admin/data-imports/ifind/{business_date}`
every trading day at 18:30 Asia/Shanghai with an `Idempotency-Key` and
`scope=pilot` (or `scope=full` after pilot acceptance). The call must use an ADMIN
credential. The returned task key is stable, so the same endpoint is also used by
an administrator to backfill or replay a date; do not invoke the worker directly.

The pilot scope is fixed to `000001.SZ`, `600000.SH`, `000300.SH`, and `000001.SH`.
Before enabling full history, retain evidence for 2025-01-02 through 2025-06-30
covering daily bars, adjustment factors, calendar, listing/delisting dates, daily
status, and effective industry membership. The mapping-permission recording,
raw/standardized manifest hashes, and quality results must all be approved by an
administrator. A missing historical field or unavailable dependency blocks the
batch and cannot be promoted to backtest/report input.

After pilot approval, schedule full imports in date and security chunks from
2016-01-01 through the latest completed trading date. Each successful batch has
its own immutable manifests and stable task key, so recovery and replay are
performed one business date at a time. No full backfill is authorized while any
pilot field, license, hash, or quality evidence is incomplete.

## Tushare primary import and AKShare validation

Tushare is the current primary HTTP source; the existing iFinD path remains available for
compatibility. Configure `APP_ENV`, `DATABASE_URL`, `AUTH_SECRET_KEY`, Redis/RQ, and a running
worker explicitly. The service fails closed when required runtime configuration is missing;
`/data-import` shows readiness/queue unavailability and keeps the task ID for manual recovery.
`JOB_QUEUE_STALE_AFTER_SECONDS` defaults to 300 seconds. It is the minimum age before an
ADMIN may recover a queued task whose database reservation succeeded but whose Redis enqueue
did not complete.

`PROVIDER_IMPORT_EXECUTION` defaults to `rq`. In `development` or `test` only, it may be set
explicitly to `background`; the application then serializes Tushare and iFinD imports on one
lifecycle-managed thread and does not require or probe Redis/RQ. Shutdown stops accepting new
imports and waits for accepted work. This mode is for one local application process only;
`simulation` and `production` reject it at configuration load and must continue using Redis/RQ.
After a background-process restart, provider imports left `RUNNING` are marked as retryable
dependency failures; `QUEUED` recovery continues to use the existing stale timeout and ADMIN
recovery action.
Configure `TUSHARE_ENABLED=true` and
provide `TUSHARE_TOKEN` only through the deployment secret environment; never pass
it in a task payload, URL, or audit record. The scheduler calls
`POST /api/v1/admin/data-imports/tushare/{business_date}?scope=pilot` at 18:30
Asia/Shanghai with an ADMIN credential and `Idempotency-Key`. The stable task key is
`data-import:{date}:tushare-{scope}`, so the same operation is used for replay and
date-by-date backfill.

The fixed pilot is `000001.SZ`, `600000.SH`, `000300.SH`, and `000001.SH`. Do not
use `scope=full` until an administrator has reviewed a continuous pilot window and
all calendar, OHLCV/amount, adjustment-factor, dated ST/suspension, listing/delisting,
and dated Shenwan industry evidence. Full backfill is one business date at a time
from 2016-01-01 through the last completed trading date.
Set `TUSHARE_FULL_ENABLED=true` only after that approval; it defaults to false. iFinD uses the
same workflow and `IFIND_FULL_ENABLED=false` by default: enable it only after its pilot evidence
has been reviewed. The page never accepts or displays either provider credential.

AKShare is validation-only. It archives calendar and unadjusted OHLCV evidence and
creates `WARNING` details for missing data, connectivity failures, or configured
price/volume/amount differences. Those warnings yield `WARNING_AVAILABLE` and do not
replace, repair, or block a complete Tushare batch. Any incomplete Tushare requirement
is `UNAVAILABLE` and blocks downstream publication.

## Failure handling

Provider submission creates a durable `queued` job before Redis/RQ enqueue. Workers claim
`queued -> running`; enqueue failures become `failed` and return HTTP 503. Dependency failures
may retry using the original task key; data-unavailable, validation, rule, and permission/state
errors do not receive blind retries. A repeated key replays a completed result and must not
duplicate plans, fees, fills, or report files. Inspect `GET /api/v1/jobs` or `job_run` for provider,
scope, business date, run number, attempt, phase, batch/quality status, and redacted error summary.
The active-task partial unique index allows only one `queued`/`running` row per task key. The
admin page preserves the failed attempt and exposes dependency retry only when the persisted
error is retryable. A stale `queued` row can be recovered through the same admin action; recovery
keeps the original job ID and task key, re-enqueues it once, and records the management audit
event. If the worker has already claimed it, the conditional state transition prevents a second
recovery enqueue.

P0: stop the affected boundary, preserve audit/log evidence, revoke exposed credentials, and escalate. P1: pause daily publication/manual confirmation, fix the dependency or data batch, then retry the same key. P2: preserve the run and schedule a normal correction. Automatic fund movement, trade execution, order repair, and risk-state recovery are prohibited.

## Backup and restore

Create backups at least hourly and verify the manifest before restore. Keep job/audit/report history and referenced data versions for 3 years. Restore into an isolated `test` environment first, run manifest and application checks, then obtain human approval for production. Target RPO ≤1 hour and RTO ≤4 hours.
