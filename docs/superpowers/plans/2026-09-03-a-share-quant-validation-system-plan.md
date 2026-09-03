# A股短线量化验证系统 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付符合 v1.1.0 基线的本地模块化单体，完成授权行情导入、历史股票池、强势趋势延续策略、风控、T+1 回测、日报、人工计划/成交、审计、异步任务、部署和质量验证闭环。

**Architecture:** 使用 FastAPI + Jinja2/HTMX 的模块化单体，领域服务通过稳定的 Python 协议、Pydantic DTO 和不可变版本快照协作。PostgreSQL/Alembic 保存关系数据，Parquet 保存行情与报告文件，Redis/RQ 承担可重试的研究和运维任务；系统没有券商交易适配器或自动下单出口。

**Tech Stack:** Python 3.12、FastAPI、Uvicorn、Pydantic、SQLAlchemy、Alembic、PostgreSQL 16 兼容 SQL、Redis 7、RQ、Pandas、PyArrow、Jinja2、HTMX、pytest、Ruff、mypy、Docker Compose。

---

## 共享实现契约

以下接口在 `foundation` worktree 先落地，其他 worktree 只能依赖它们，不得各自复制一套语义：

```python
class DataUnavailableError(Exception): ...
class RuleViolationError(Exception): ...
class StateConflictError(Exception): ...

class HistoricalUniverseBuilder(Protocol):
    def build(self, as_of_date: date, data_batch_id: UUID) -> list[UniverseMember]: ...

class StrategyDefinition(Protocol):
    def evaluate(self, snapshot: SignalInput) -> list[SignalResult]: ...

class RiskAllocator(Protocol):
    def allocate(self, signals: Sequence[SignalResult], account: AccountSnapshot) -> list[OrderPlanDraft]: ...

class ExecutionSimulator(Protocol):
    def simulate(self, plan: OrderPlanDraft, market: ExecutionMarketSnapshot) -> ExecutionResult: ...
```

共同不变量：金额/价格/费率全部使用 `Decimal`；数量为整数；买入数量满足主板 100 股单位；信号读取 `trade_date <= as_of_date` 且 `available_at <= information_cutoff_at`；成交日期晚于信号日期；现金和持仓不能为负；已发布版本、运行、计划、成交和审计记录不能覆盖或物理删除。

### Task 1: 基础工程、配置、认证、权限与审计（foundation）

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `app/main.py`
- Create: `app/core/config.py`
- Create: `app/core/errors.py`
- Create: `app/core/contracts.py`
- Create: `app/core/logging.py`
- Create: `app/core/security.py`
- Create: `app/core/dependencies.py`
- Create: `app/api/errors.py`
- Create: `app/api/health.py`
- Create: `app/api/auth.py`
- Create: `app/api/audit.py`
- Create: `tests/unit/test_config.py`
- Create: `tests/unit/test_security.py`
- Create: `tests/api/test_health.py`

- [ ] **Step 1: Define the dependency and configuration contract**

Add runtime dependencies for FastAPI, Uvicorn, Pydantic settings, SQLAlchemy, Alembic, psycopg, Redis/RQ, Pandas, PyArrow, Jinja2, passlib/Argon2, python-jose, pytest, httpx, Ruff and mypy. Define environment settings for `APP_ENV`, `DATABASE_URL`, `QUEUE_URL`, `OBJECT_STORE_PATH`, `DATA_HISTORY_START`, `BENCHMARK_PRIMARY`, `BENCHMARK_SECONDARY`, `DEFAULT_INITIAL_EQUITY`, `MAX_INVESTMENT_RATIO`, `MAX_POSITIONS`, `DRAWDOWN_WARNING`, `DRAWDOWN_STOP`, `EXECUTION_PRICE_MODE`, `PARTIAL_FILL_MODE`, `EXPORT_RETENTION_DAYS` and secret references. Reject missing required values, non-Decimal numeric formats and any environment override that attempts to mutate a published version.

- [ ] **Step 2: Write failing tests for startup and health behavior**

`tests/unit/test_config.py` must assert that the defaults are 2016-01-01, `000300.SH`, `000001.SH`, Decimal 20000.00, 0.70, 4, 0.06 and 0.08; invalid risk thresholds and unsupported instruments raise a validation error. `tests/api/test_health.py` must assert `GET /health` returns dependency-neutral process health and `GET /api/v1/health/readiness` reports database/queue checks without exposing credentials.

- [ ] **Step 3: Implement errors, request context and structured logging**

Implement the five domain exceptions and an API handler returning the documented `{error, request_id}` structure. Generate or propagate `X-Request-Id`; require `Idempotency-Key` on mutating routes; emit JSON logs with `timestamp`, `level`, `request_id`, `run_id`, `job_id`, `user_id`, `event_code` and `message`, masking tokens, passwords, queue URLs and secret references.

- [ ] **Step 4: Implement local auth and RBAC**

Use Argon2id password hashes. Issue access tokens for 30 minutes and refresh tokens for 7 days. Store revocation/session state so logout, disabled accounts and administrator revocation take effect immediately. Implement `USER`, `REVIEWER` and `ADMIN` checks with resource-level authorization and a non-leaking 404/403 policy. Do not add broker credentials or order-submit permissions.

- [ ] **Step 5: Implement audit persistence boundary and health routes**

Expose an append-only `AuditWriter` protocol and route all later state-changing actions through it. Add health and readiness endpoints plus an app factory that can run with dependency overrides in unit tests. Keep the database implementation behind an infrastructure adapter so later migrations can supply the concrete table.

- [ ] **Step 6: Run foundation checks and commit**

Run:

```text
F:\PROJECT\Money.venv\Scripts\python.exe -m pytest tests/unit/test_config.py tests/unit/test_security.py tests/api/test_health.py -q
F:\PROJECT\Money.venv\Scripts\python.exe -m ruff check app tests
F:\PROJECT\Money.venv\Scripts\python.exe -m mypy app
```

Expected: all selected tests pass, Ruff has no errors, and mypy has no errors. Commit as `feat(foundation): add runtime contract authentication and audit boundary`.

### Task 2: 数据库、迁移、数据导入与质量门禁（data）

**Files:**
- Create: `app/infrastructure/db/base.py`
- Create: `app/infrastructure/db/session.py`
- Create: `app/infrastructure/db/models/identity.py`
- Create: `app/infrastructure/db/models/data.py`
- Create: `app/infrastructure/db/models/market.py`
- Create: `app/infrastructure/db/models/configuration.py`
- Create: `app/infrastructure/db/models/strategy.py`
- Create: `app/infrastructure/db/models/backtest.py`
- Create: `app/infrastructure/db/models/portfolio.py`
- Create: `app/infrastructure/db/models/operations.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/0001_initial_schema.py`
- Create: `app/domain/data/importer.py`
- Create: `app/domain/data/quality.py`
- Create: `app/domain/data/calendar.py`
- Create: `app/domain/data/universe.py`
- Create: `tests/unit/test_data_quality.py`
- Create: `tests/unit/test_historical_universe.py`
- Create: `tests/integration/test_migrations.py`
- Create: `tests/fixtures/market_minimal.csv`

- [ ] **Step 1: Write failing quality and historical-state tests**

Fixtures must contain a normal main-board stock, a listing younger than 60 trading days, historical ST, suspension, delisting-period and later-delisted records, a missing trading day, duplicate security/date, zero/negative price, abnormal volume, missing adjustment factor and an industry change. Assert errors identify security, date and field; error-level findings set the batch unavailable; historical universe decisions use the as-of state rather than today’s security list.

- [ ] **Step 2: Implement SQLAlchemy models and constraints**

Create the tables specified by the database design: users/roles, data sources/batches, calendar, securities, status and industry histories, daily bars, adjustment factors, rule/cost/strategy versions, signal snapshots, runs/stages, portfolio/position snapshots, plans/executions/ledger, metrics/reports, daily reports, jobs, audits and alerts. Use UUIDs, timezone-aware timestamps, `Numeric(20, 6)`/`Numeric(20, 10)`, immutable version fields, uniqueness constraints and indexes for date/run/execution lookups. Enforce database-safe non-negative quantities where PostgreSQL can do so; keep cross-row business checks in domain services.

- [ ] **Step 3: Implement the first Alembic migration**

Generate one forward migration containing the complete baseline schema and a downgrade that drops only objects created by that migration. Do not rewrite the v1.1.0 documents or add destructive data cleanup. The migration must run against PostgreSQL 16-compatible SQL and SQLite test fixtures only where the type adapter permits it.

- [ ] **Step 4: Implement canonical hashing and CSV/Parquet import**

Normalize field order, dates, Decimal values and null representation before computing SHA-256. Import only authorized CSV/Parquet paths; persist source metadata, version, row counts, date coverage, `available_at`, `information_cutoff_at`, adjustment convention and content hash. Never silently overwrite a prior batch or fill missing state from a later date.

- [ ] **Step 5: Implement data-quality gate and historical universe**

Add checks for calendar continuity, duplicates, non-positive prices, volume/amount anomalies, adjustment factor presence, status completeness and date/cutoff violations. Implement `build_historical_universe(as_of_date)` for main-board ordinary A shares, excluding ST/*ST, suspension, delist period, under-60-trading-day and liquidity-below-20-day-median-20-million records, returning an explicit exclusion reason for every rejected member.

- [ ] **Step 6: Run migration and data checks, then commit**

Run:

```text
F:\PROJECT\Money.venv\Scripts\python.exe -m pytest tests/unit/test_data_quality.py tests/unit/test_historical_universe.py tests/integration/test_migrations.py -q
F:\PROJECT\Money.venv\Scripts\python.exe -m alembic upgrade head
F:\PROJECT\Money.venv\Scripts\python.exe -m ruff check app tests
```

Expected: the quality gate blocks every error fixture, historical state tests pass, and the migration reaches head without changing the baseline documents. Commit as `feat(data): add versioned market schema import and quality gates`.

### Task 3: 股票池、策略、风控、成交模拟与回测（engine）

**Files:**
- Create: `app/domain/strategy/strong_trend.py`
- Create: `app/domain/strategy/features.py`
- Create: `app/domain/risk/allocation.py`
- Create: `app/domain/risk/drawdown.py`
- Create: `app/domain/execution/costs.py`
- Create: `app/domain/execution/simulator.py`
- Create: `app/domain/portfolio/ledger.py`
- Create: `app/domain/backtest/runner.py`
- Create: `app/domain/backtest/metrics.py`
- Create: `tests/unit/test_strong_trend.py`
- Create: `tests/unit/test_risk_allocation.py`
- Create: `tests/unit/test_drawdown.py`
- Create: `tests/unit/test_execution_simulator.py`
- Create: `tests/unit/test_ledger.py`
- Create: `tests/integration/test_backtest_reproducibility.py`
- Create: `tests/fixtures/golden_backtest.json`

- [ ] **Step 1: Write failing strategy and future-function tests**

Assert the market switch uses the benchmark close above its 20-day moving average; candidate conditions use close above 10-day average, 5-day return inclusive 2%–12%, 5/20 amount ratio inclusive 1.2–3.0 and reliable non-limit-up buying. Assert the weighted score is 50% 5-day return rank + 25% 20-day return rank + 25% amount-expansion rank, ties are deterministic, and missing features are rejected rather than treated as zero. Inject extreme T+1 data and assert the T-day signal is unchanged.

- [ ] **Step 2: Implement feature, signal and sell-trigger calculations**

Use only rows satisfying the shared time cutoff. Return feature provenance, boolean condition results, exclusion reasons and score. Keep signal objects independent from target quantity. Implement sell triggers for holding at least three trading days, falling outside the top 50% candidate ranking, loss of 4% from entry and market switch closed for two consecutive days; all triggers schedule the next tradable date.

- [ ] **Step 3: Write failing risk and drawdown tests**

Cover 20,000 initial equity, 70% investment cap, four positions, 3,500 target per security, 20% single-security cap, 35% industry cap, 1% single-trade risk, 100-share rounding and cash-not-negative. At exactly 6% drawdown assert `STOP_NEW` with sells allowed; at exactly 8% assert `REVIEW_REQUIRED` with new buys blocked; restoration requires two confirmations and an audit event.

- [ ] **Step 4: Implement risk allocation and server-side state machine**

Apply constraints without changing source scores. Return accepted/reduced/rejected drafts with every calculation and reason in `risk_snapshot`. Reject margin, leverage, short and non-cash account types. Implement explicit risk transitions; no API or job may transition `REVIEW_REQUIRED` to `RESTORED` without the checklist and second confirmation.

- [ ] **Step 5: Write failing execution, fee and ledger tests**

Cover `NEXT_OPEN_ADJUSTED`: buy price `open * 1.002`, sell price `open * 0.998`; missing open, suspension, buy limit-up and sell limit-down are not assumed filled. Use `FULL_OR_NONE` for backtest partial-fill uncertainty and allow partial quantities for manual execution input. Assert commission is `max(notional*0.00030, 5)`, sell stamp tax is `notional*0.00050`, transfer fee is bidirectional `notional*0.00001`, and included regulatory/handling fees are not double-counted. Assert unfilled orders do not change cash or positions and every filled transaction reconciles to the ledger.

- [ ] **Step 6: Implement execution simulator, ledger and metrics**

Build immutable execution results with status, fill quantity, unfilled quantity, reason, adjusted price and fee breakdown. Update cash, positions, available quantity, equity, high watermark and drawdown in one transaction boundary. Compute net return, drawdown, monthly/yearly returns, Sharpe, win rate, profit/loss ratio, holding period, turnover, cost and industry exposure with explicit unavailable markers for insufficient data.

- [ ] **Step 7: Implement chronological backtest and golden reproducibility**

Run data gate → historical universe → signal → risk → T+1 execution → ledger → report in chronological order. Support train/validation/OOS and rolling windows without random shuffling. Bind run ID, data/strategy/cost/rule versions, benchmarks, time range, initial equity, parameter snapshot and content hash. Run the fixed fixture twice and compare orders, ledger, equity series, metrics and hashes byte-for-byte.

- [ ] **Step 8: Run engine checks and commit**

Run:

```text
F:\PROJECT\Money.venv\Scripts\python.exe -m pytest tests/unit/test_strong_trend.py tests/unit/test_risk_allocation.py tests/unit/test_drawdown.py tests/unit/test_execution_simulator.py tests/unit/test_ledger.py tests/integration/test_backtest_reproducibility.py -q
F:\PROJECT\Money.venv\Scripts\python.exe -m ruff check app tests
F:\PROJECT\Money.venv\Scripts\python.exe -m mypy app
```

Expected: all edge-case and golden tests pass with no future-data path. Commit as `feat(engine): add reproducible strategy risk and backtest domain`.

### Task 4: API、页面、日报、人工计划和成交（api）

**Files:**
- Create: `app/api/v1/data.py`
- Create: `app/api/v1/strategies.py`
- Create: `app/api/v1/backtests.py`
- Create: `app/api/v1/operations.py`
- Create: `app/api/v1/admin.py`
- Create: `app/schemas/common.py`
- Create: `app/schemas/data.py`
- Create: `app/schemas/strategy.py`
- Create: `app/schemas/backtest.py`
- Create: `app/schemas/operations.py`
- Create: `app/templates/base.html`
- Create: `app/templates/login.html`
- Create: `app/templates/dashboard.html`
- Create: `app/templates/data_quality.html`
- Create: `app/templates/universe.html`
- Create: `app/templates/strategy_versions.html`
- Create: `app/templates/backtest_create.html`
- Create: `app/templates/backtest_detail.html`
- Create: `app/templates/daily_report.html`
- Create: `app/templates/order_plans.html`
- Create: `app/templates/executions.html`
- Create: `app/templates/reports.html`
- Create: `app/templates/admin.html`
- Create: `tests/api/test_data_routes.py`
- Create: `tests/api/test_backtest_routes.py`
- Create: `tests/api/test_operations_routes.py`
- Create: `tests/api/test_rbac_routes.py`
- Create: `tests/e2e/test_daily_flow.py`

- [ ] **Step 1: Write failing API contract tests**

Test documented paths under `/api/v1`: data batches/quality, historical pool, strategies/review/diff, backtests/detail/trades/report, daily reports, order plans, confirm, executions, account snapshots, exports, jobs and audit events. Assert common success/error envelopes, request IDs, pagination, string-serialized Decimal values, idempotency, optimistic `expected_version`, 403/404 policy and absence of order-submit/broker endpoints.

- [ ] **Step 2: Implement Pydantic DTOs and route dependencies**

Define request/response models with date and Decimal validation, supported-market checks and non-negative fees. Wire auth, role checks, resource lookup, idempotency service and audit writer into the routers. Return `202 Accepted` for queued backtests and preserve the same run ID on identical idempotent retries.

- [ ] **Step 3: Implement data, strategy and backtest routes**

Implement import-batch creation, quality report, dated historical pool, strategy draft/review/diff, backtest preflight/creation, run detail/stages, paginated trades and report queries. Block runs when data, cost or rule versions are unavailable; never mutate historical run results.

- [ ] **Step 4: Implement daily-report, plan and manual-execution routes**

Expose candidates, holdings, next-day plans, confirmation/skip with review note, actual execution entry and account snapshots. Enforce plan expiry at next trading day 09:25, risk-state revalidation, 100-share buy rules and independent plan/execution states. Manual entries may be partial; duplicate idempotency keys return the original result without duplicate ledger writes.

- [ ] **Step 5: Implement pages and fixed compliance notices**

Create Jinja2/HTMX pages matching the product flow. Every page shows data date, quality, strategy/cost/rule versions, run number, risk state, “不构成投资建议”, “不承诺收益” and “不自动下单”. Plan controls request confirmation only; there is no submit-order form or broker credential field.

- [ ] **Step 6: Run API and end-to-end checks, then commit**

Run:

```text
F:\PROJECT\Money.venv\Scripts\python.exe -m pytest tests/api tests/e2e/test_daily_flow.py -q
F:\PROJECT\Money.venv\Scripts\python.exe -m ruff check app tests
F:\PROJECT\Money.venv\Scripts\python.exe -m mypy app
```

Expected: contract, authorization, idempotency and daily-flow tests pass. Commit as `feat(api): add versioned research and manual execution interfaces`.

### Task 5: 异步任务、部署、备份、恢复与可观测性（ops）

**Files:**
- Create: `app/jobs/queue.py`
- Create: `app/jobs/tasks.py`
- Create: `app/jobs/idempotency.py`
- Create: `app/infrastructure/storage/parquet_store.py`
- Create: `scripts/import_data.py`
- Create: `scripts/run_backtest.py`
- Create: `scripts/run_daily.py`
- Create: `scripts/backup.py`
- Create: `scripts/restore_check.py`
- Create: `docker-compose.yml`
- Create: `Dockerfile`
- Create: `README.md`
- Create: `docs/runbooks/deployment.md`
- Create: `docs/runbooks/operations.md`
- Create: `tests/unit/test_jobs.py`
- Create: `tests/integration/test_idempotent_jobs.py`
- Create: `tests/integration/test_backup_manifest.py`

- [ ] **Step 1: Write failing queue and idempotency tests**

Assert job keys are deterministic for calendar/data-quality/daily-report/backtest/backup scopes, retryable dependency failures reuse the same key, data/rule errors are not blindly retried, duplicate daily runs do not create duplicate plans or ledger entries, and failed jobs retain error summaries and stage context.

- [ ] **Step 2: Implement Redis/RQ task boundaries**

Add tasks for calendar refresh, authorized data import, quality gate, daily signal/report, queued backtest and backup. Configure retry count/backoff only for dependency failures. Persist `job_run` status, attempts, business date, run ID and idempotency key; never run broker actions.

- [ ] **Step 3: Implement Parquet storage, scripts and backup manifest**

Write versioned raw/standardized Parquet paths, content hashes and report artifacts. Scripts must load settings from environment, use the configured Python runtime, avoid printing secrets and support dry-run validation. Backup manifests include scope, timestamp, hashes and restore-check result; restore checks must verify schema, constraints, ledger reconciliation and snapshot reproducibility.

- [ ] **Step 4: Implement Docker Compose and operations documentation**

Compose services are `app`, `worker`, `postgres` pinned to PostgreSQL 16-compatible image and `redis` pinned to Redis 7-compatible image. Bind application to loopback by default, use environment-only secrets, separate DEV/TEST/SIM volumes, expose health checks and keep no broker network/API service. Document migration, deployment, rollback, RPO ≤1 hour, RTO ≤4 hours, retention and P0/P1/P2 response.

- [ ] **Step 5: Run operations checks and commit**

Run:

```text
F:\PROJECT\Money.venv\Scripts\python.exe -m pytest tests/unit/test_jobs.py tests/integration/test_idempotent_jobs.py tests/integration/test_backup_manifest.py -q
docker compose config
F:\PROJECT\Money.venv\Scripts\python.exe -m ruff check app tests scripts
```

Expected: task idempotency and manifest tests pass, Compose configuration is valid, and no secret is embedded in YAML or logs. Commit as `feat(ops): add idempotent jobs deployment and recovery tooling`.

### Task 6: 测试、质量追踪与主分支集成（tests/coordinator）

**Files:**
- Create: `tests/fixtures/complete_minimal_dataset/`
- Create: `tests/integration/test_full_daily_flow.py`
- Create: `tests/security/test_no_broker_path.py`
- Create: `tests/security/test_secret_redaction.py`
- Create: `tests/performance/test_daily_budget.py`
- Create: `scripts/run_quality_checks.py`
- Modify: `08-测试计划与测试用例.md` only to add dated execution evidence after tests actually run
- Modify: `13-需求测试追踪矩阵.md` only to add evidence/status after tests actually run

- [ ] **Step 1: Build complete deterministic fixture**

Include history from before and after a delisting, ST/suspension/industry changes, benchmark data, T/T+1 open prices, limit-up/limit-down and missing-price cases. Store a manifest SHA-256 and do not label unexecuted cases as passing.

- [ ] **Step 2: Write end-to-end and security tests**

Exercise import → quality → historical pool → signal → risk → next-day plan → simulated/manual execution → ledger → daily report → export. Assert run/version/hash propagation, no future data, no survivor bias, no negative cash/positions, correct costs, manual confirmation separation, RBAC, one-time export token, redacted logs and absence of any broker order-submit route.

- [ ] **Step 3: Create the required Python 3.12 virtual environment**

Run:

```text
D:\Dev\py\python.exe -m venv F:\PROJECT\Money.venv
F:\PROJECT\Money.venv\Scripts\python.exe -m pip install -e .[dev]
```

Use `F:\PROJECT\Money.venv\Scripts\python.exe` for all following Python, pytest, Ruff and mypy commands. Do not commit the virtual environment.

- [ ] **Step 4: Integrate worktrees in dependency order**

Review each subtask’s actual diff and required report before integration. Merge foundation first, then data, engine, api and ops; integrate tests after interfaces stabilize. Resolve conflicts only in the coordinator worktree and preserve append-only behavior. After each merge run the affected tests; do not accept a green report without inspecting the diff.

- [ ] **Step 5: Run the full verification gate**

Run:

```text
F:\PROJECT\Money.venv\Scripts\python.exe -m pytest -q
F:\PROJECT\Money.venv\Scripts\python.exe -m ruff check app tests scripts
F:\PROJECT\Money.venv\Scripts\python.exe -m ruff format --check app tests scripts
F:\PROJECT\Money.venv\Scripts\python.exe -m mypy app
F:\PROJECT\Money.venv\Scripts\python.exe -m alembic check
docker compose config
```

Expected: all runnable tests pass; static checks, formatting, migration check and Compose validation pass. Any unavailable external dependency must be recorded as blocked evidence, not changed to pass.

- [ ] **Step 6: Run a local smoke test and inspect the final diff**

Start the app bound to loopback, call health/readiness and the documented login/data/backtest/report paths with the deterministic fixture, then stop it. Inspect `git diff --stat`, `git diff --check`, changed-file ownership, secret scans and the final test logs. Update the trace matrix and test plan only with command, date, environment, fixture hash and observed result.

- [ ] **Step 7: Commit the integrated release candidate**

Commit as `release: integrate quant validation MVP` only after P0 checks are green: future-function protection, ledger reconciliation, permission isolation, no automatic order path, non-negative cash/positions, correct rule version selection, data-quality blocking and reproducibility. Keep the goal active if any required item remains unverified.

## Completion evidence required

The coordinator must retain, before claiming completion:

1. The six subtask reports listing modified files, implementation, API/database changes, commands, tests, incomplete work and integration notes.
2. Actual branch/worktree diffs for every subtask and a clean final coordinator diff review.
3. A Python 3.12 virtual environment at `F:\PROJECT\Money.venv` and reproducible install command.
4. Passing unit, integration, API, security, data-accuracy, backtest-realism and end-to-end tests, with performance status recorded rather than assumed.
5. Valid Alembic migration, Docker Compose configuration, README, deployment/rollback and operations runbooks.
6. Evidence that no `.env`, password, data-source secret, broker credential or automatic order endpoint entered Git.
7. Updated test/trace documents containing only executed evidence; unexecuted cases remain “待执行”.
