# Operations runbook

## Daily close

1. Confirm the authorized data batch and calendar are available.
2. Run calendar update, import, and quality jobs with the business date. An error-level quality result blocks downstream work.
3. Run the daily signal/report job. Review data date, strategy/cost/rule versions, run number, risk state, and warnings.
4. Treat order plans as review documents only. Human confirmation and actual fills remain separate records; no job submits, cancels, repairs, or automatically recovers an order.
5. Verify the report artifact manifest and record the operator review.

## Failure handling

Dependency failures may retry using the same idempotency key. Data-unavailable, validation, rule, and state errors do not receive blind retries. A repeated key replays a completed result and must not duplicate plans, fees, fills, or report files. Inspect `job_run` for business date, run number, attempt, stage, and redacted error summary.

P0: stop the affected boundary, preserve audit/log evidence, revoke exposed credentials, and escalate. P1: pause daily publication/manual confirmation, fix the dependency or data batch, then retry the same key. P2: preserve the run and schedule a normal correction. Automatic fund movement, trade execution, order repair, and risk-state recovery are prohibited.

## Backup and restore

Create backups at least hourly and verify the manifest before restore. Keep job/audit/report history and referenced data versions for 3 years. Restore into an isolated `test` environment first, run manifest and application checks, then obtain human approval for production. Target RPO ≤1 hour and RTO ≤4 hours.
