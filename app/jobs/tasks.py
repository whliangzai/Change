"""Named job entry points; domain/application services are injected callables."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from app.core.contracts import AuditWriter
from app.jobs.idempotency import JobResult, JobRunStore, run_idempotent_task


def _run(
    kind: str,
    business_date: date,
    service: Callable[..., Any],
    *,
    run_store: JobRunStore | None,
    audit_writer: AuditWriter | None,
    scope: str | None = None,
    **kwargs: Any,
) -> JobResult:
    return run_idempotent_task(
        kind,
        business_date,
        lambda: service(business_date=business_date, **kwargs),
        run_store=run_store,
        audit_writer=audit_writer,
        scope=scope,
    )


def update_calendar(
    business_date: date,
    service: Callable[..., Any],
    *,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run("calendar", business_date, service, run_store=run_store, audit_writer=audit_writer)


def import_authorized_data(
    business_date: date,
    service: Callable[..., Any],
    *,
    source_path: str,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run(
        "data-import",
        business_date,
        service,
        source_path=source_path,
        run_store=run_store,
        audit_writer=audit_writer,
    )


def validate_data_quality(
    business_date: date,
    service: Callable[..., Any],
    *,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run("data-quality", business_date, service, run_store=run_store, audit_writer=audit_writer)


def run_daily_report(
    business_date: date,
    service: Callable[..., Any],
    *,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run("daily-report", business_date, service, run_store=run_store, audit_writer=audit_writer)


def run_backtest(
    business_date: date,
    service: Callable[..., Any],
    *,
    scope: str,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run(
        "backtest", business_date, service, scope=scope, run_store=run_store, audit_writer=audit_writer
    )


def run_backup(
    business_date: date,
    service: Callable[..., Any],
    *,
    scope: str = "data",
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run(
        "backup", business_date, service, scope=scope, run_store=run_store, audit_writer=audit_writer
    )


__all__ = [
    "import_authorized_data",
    "run_backtest",
    "run_backup",
    "run_daily_report",
    "run_idempotent_task",
    "update_calendar",
    "validate_data_quality",
]
