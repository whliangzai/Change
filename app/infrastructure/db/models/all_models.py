"""Core schema models for reproducible, append-first market research."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, UUIDPrimaryKeyMixin


class UserAccount(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_account"
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Role(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "role"
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class UserRole(Base):
    __tablename__ = "user_role"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("role.id"), primary_key=True)


class DataSource(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "data_source"
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    secret_ref: Mapped[str | None] = mapped_column(String(256))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DataBatch(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "data_batch"
    __table_args__ = (UniqueConstraint("source_id", "dataset_type", "as_of_date", "version", name="uq_data_batch_source_dataset_date_version"),)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("data_source.id"), nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    information_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="VALIDATING")
    quality_summary: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TradeCalendar(Base):
    __tablename__ = "trade_calendar"
    exchange: Mapped[str] = mapped_column(String(8), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prev_trade_date: Mapped[date | None] = mapped_column(Date)
    next_trade_date: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (Index("ix_trade_calendar_next_trade_date", "exchange", "next_trade_date"),)


class Security(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "security"
    __table_args__ = (UniqueConstraint("exchange", "symbol", name="uq_security_exchange_symbol"),)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    security_type: Mapped[str] = mapped_column(String(16), nullable=False)
    list_date: Mapped[date] = mapped_column(Date, nullable=False)
    delist_date: Mapped[date | None] = mapped_column(Date)


class SecurityStatusHistory(Base):
    __tablename__ = "security_status_history"
    security_id: Mapped[UUID] = mapped_column(ForeignKey("security.id"), primary_key=True)
    effective_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_st: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_delist_period: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    board: Mapped[str] = mapped_column(String(16), nullable=False)
    source_batch_id: Mapped[UUID] = mapped_column(ForeignKey("data_batch.id"), nullable=False)


class IndustryMembershipHistory(Base):
    __tablename__ = "industry_membership_history"
    security_id: Mapped[UUID] = mapped_column(ForeignKey("security.id"), primary_key=True)
    industry_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    effective_from: Mapped[date] = mapped_column(Date, primary_key=True)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_batch_id: Mapped[UUID] = mapped_column(ForeignKey("data_batch.id"), nullable=False)
    __table_args__ = (Index("ix_industry_membership_history_dates", "security_id", "effective_from", "effective_to"),)


class DailyBar(Base):
    __tablename__ = "daily_bar"
    security_id: Mapped[UUID] = mapped_column(ForeignKey("security.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    raw_open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    raw_high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    raw_low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    raw_close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    adjusted_open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    adjusted_high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    adjusted_low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    adjusted_close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    adjust_factor: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    data_batch_id: Mapped[UUID] = mapped_column(ForeignKey("data_batch.id"), nullable=False)
    __table_args__ = (Index("ix_daily_bar_trade_date_security", "trade_date", "security_id"),)


class AdjustmentFactor(Base):
    __tablename__ = "adjustment_factor"
    security_id: Mapped[UUID] = mapped_column(ForeignKey("security.id"), primary_key=True)
    effective_date: Mapped[date] = mapped_column(Date, primary_key=True)
    factor: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    factor_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    data_batch_id: Mapped[UUID] = mapped_column(ForeignKey("data_batch.id"), nullable=False)


class RuleConfigVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "rule_config_version"
    __table_args__ = (UniqueConstraint("scope", "version", name="uq_rule_config_scope_version"),)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class CostConfigVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "cost_config_version"
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    commission_min: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    stamp_tax_sell_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    transfer_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    regulatory_fee_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    handling_fee_rate: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    commission_includes_regulatory: Mapped[bool] = mapped_column(Boolean, nullable=False)
    commission_includes_handling: Mapped[bool] = mapped_column(Boolean, nullable=False)
    slippage_buy: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    slippage_sell: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    execution_price_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    partial_fill_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class StrategyVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "strategy_version"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_strategy_code_version"), Index("ix_strategy_version_status", "status"))
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    parameters: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    change_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SignalSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "signal_snapshot"
    __table_args__ = (UniqueConstraint("strategy_version_id", "data_batch_id", "as_of_date", "security_id", name="uq_signal_snapshot_input"), Index("ix_signal_snapshot_date_score", "as_of_date", "score"))
    strategy_version_id: Mapped[UUID] = mapped_column(ForeignKey("strategy_version.id"), nullable=False)
    data_batch_id: Mapped[UUID] = mapped_column(ForeignKey("data_batch.id"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    information_cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    security_id: Mapped[UUID] = mapped_column(ForeignKey("security.id"), nullable=False)
    features: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    eligibility: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(20, 10), nullable=False)
    exclusion_reason: Mapped[str | None] = mapped_column(String(128))


class BacktestRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "backtest_run"
    run_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    data_batch_id: Mapped[UUID] = mapped_column(ForeignKey("data_batch.id"), nullable=False)
    strategy_version_id: Mapped[UUID] = mapped_column(ForeignKey("strategy_version.id"), nullable=False)
    cost_config_id: Mapped[UUID] = mapped_column(ForeignKey("cost_config_version.id"), nullable=False)
    rule_config_id: Mapped[UUID] = mapped_column(ForeignKey("rule_config_version.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date | None] = mapped_column(Date)
    valid_end: Mapped[date | None] = mapped_column(Date)
    oos_start: Mapped[date | None] = mapped_column(Date)
    benchmark_symbol: Mapped[str] = mapped_column(String(16), nullable=False, default="000300.SH")
    secondary_benchmark_symbol: Mapped[str] = mapped_column(String(16), nullable=False, default="000001.SH")
    universe_benchmark_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    execution_price_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    initial_equity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    config_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result_usable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_backtest_run_status_created_at", "status", "created_at"),)


class RunStage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "run_stage"
    __table_args__ = (UniqueConstraint("run_id", "stage_code", name="uq_run_stage_code"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    stage_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_summary: Mapped[str | None] = mapped_column(Text)


class PortfolioSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "portfolio_snapshot"
    __table_args__ = (UniqueConstraint("run_id", "account_id", "trade_date", name="uq_portfolio_snapshot_run_account_date"), Index("ix_portfolio_snapshot_trade_date", "trade_date"))
    run_id: Mapped[UUID] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    account_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high_watermark: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    risk_state: Mapped[str] = mapped_column(String(24), nullable=False)


class PositionSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "position_snapshot"
    __table_args__ = (UniqueConstraint("portfolio_snapshot_id", "security_id", name="uq_position_snapshot_security"),)
    portfolio_snapshot_id: Mapped[UUID] = mapped_column(ForeignKey("portfolio_snapshot.id"), nullable=False)
    security_id: Mapped[UUID] = mapped_column(ForeignKey("security.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    available_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    market_price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    industry_code: Mapped[str | None] = mapped_column(String(32))


class OrderPlan(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "order_plan"
    __table_args__ = (UniqueConstraint("plan_no", name="uq_order_plan_no"), UniqueConstraint("idempotency_key", name="uq_order_plan_idempotency_key"), Index("ix_order_plan_execution_status", "execution_date", "status"))
    plan_no: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    execution_date: Mapped[date] = mapped_column(Date, nullable=False)
    security_id: Mapped[UUID] = mapped_column(ForeignKey("security.id"), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    planned_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    reference_high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    trigger_reasons: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    risk_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class ExecutionRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "execution_record"
    execution_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("order_plan.id"), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(16), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    stamp_tax: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    transfer_fee: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    other_fee: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unfilled_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unfilled_reason: Mapped[str | None] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (Index("ix_execution_record_plan_executed_at", "plan_id", "executed_at"),)


class LedgerEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ledger_entry"
    account_id: Mapped[UUID] = mapped_column(ForeignKey("user_account.id"), nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(24), nullable=False)
    security_id: Mapped[UUID | None] = mapped_column(ForeignKey("security.id"))
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_delta: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    fee_delta: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_ledger_entry_account_trade_date", "account_id", "trade_date"),)


class PerformanceMetric(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "performance_metric"
    __table_args__ = (UniqueConstraint("run_id", "segment", "metric_code", name="uq_performance_metric_run_segment_code"),)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    segment: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_value: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    calculation_note: Mapped[str | None] = mapped_column(Text)


class ReportArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "report_artifact"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(24), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_report_artifact_run_type", "run_id", "report_type"),)


class DailyReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "daily_report"
    __table_args__ = (UniqueConstraint("report_date", "run_id", name="uq_daily_report_date_run"),)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("backtest_run.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    market_switch: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    result_usable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class JobRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_run"
    __table_args__ = (UniqueConstraint("job_code", "idempotency_key", name="uq_job_run_job_idempotency"),)
    job_code: Mapped[str] = mapped_column(String(32), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_event"
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("user_account.id"))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before_digest: Mapped[str | None] = mapped_column(Text)
    after_digest: Mapped[str | None] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    __table_args__ = (Index("ix_audit_event_object_time", "object_type", "object_id", "occurred_at"), Index("ix_audit_event_actor_time", "actor_user_id", "occurred_at"))


class SystemAlert(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_alert"
    alert_code: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_system_alert_status_severity", "status", "severity"),)
