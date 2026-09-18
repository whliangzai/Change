"""Named job entry points; domain/application services are injected callables."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date
from typing import Any, NoReturn

from app.core.contracts import AuditWriter
from app.core.errors import DependencyError
from app.jobs.idempotency import JobResult, JobRunStore, run_idempotent_task


def _run(
    kind: str,
    business_date: date,
    service: Callable[..., Any],
    *,
    run_store: JobRunStore | None,
    audit_writer: AuditWriter | None,
    scope: str | None = None,
    phase: str = "execute",
    service_kwargs: dict[str, Any] | None = None,
    **kwargs: Any,
) -> JobResult:
    operation_kwargs = dict(kwargs)
    operation_kwargs.update(service_kwargs or {})
    return run_idempotent_task(
        kind,
        business_date,
        lambda: service(business_date=business_date, **operation_kwargs),
        run_store=run_store,
        audit_writer=audit_writer,
        scope=scope,
        phase=phase,
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


def import_ifind_data(
    business_date: date,
    service: Callable[..., Any] | Any,
    *,
    scope: str,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    """Run one iFinD import with a stable, scope-qualified job key.

    The provider scope is passed to the service while the job scope is prefixed so
    pilot and full imports for the same date cannot collide with one another or with
    legacy authorized-data jobs.
    """
    if scope not in {"pilot", "full"}:
        raise ValueError("scope must be pilot or full")
    operation = getattr(service, "import_business_date", service)
    return _run(
        "data-import",
        business_date,
        operation,
        scope=f"ifind-{scope}",
        phase="provider-import",
        service_kwargs={"scope": scope},
        run_store=run_store,
        audit_writer=audit_writer,
    )


def _record_provider_failure(
    business_date: date,
    provider: str,
    scope: str,
    error: BaseException,
    *,
    run_store: JobRunStore,
    audit_writer: AuditWriter,
) -> NoReturn:
    """Move a queued provider job through running to failed before re-raising."""

    def fail() -> None:
        raise error

    run_idempotent_task(
        "data-import",
        business_date,
        fail,
        scope=f"{provider}-{scope}",
        phase="provider-import",
        run_store=run_store,
        audit_writer=audit_writer,
    )
    raise error


def run_ifind_import(business_date: date | str, scope: str) -> JobResult:
    """RQ entry point that constructs the iFinD runtime service in the worker."""
    if scope not in {"pilot", "full"}:
        raise ValueError("scope must be pilot or full")
    from app.application.ifind_ingestion import IFindIngestionService
    from app.core.config import ConfigurationError, load_settings
    from app.infrastructure.db.session import make_engine
    from app.infrastructure.ifind import IFindHttpClient, IFindRawArchive
    from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
    from app.infrastructure.repositories.runtime import (
        SqlAlchemyAuditWriter,
        SqlAlchemyJobRunStore,
    )

    normalized_date = date.fromisoformat(str(business_date)[:10])
    settings = load_settings()
    engine = make_engine(settings.database_url)
    repository = SqlAlchemyResearchRepository.from_engine(
        engine, create_schema=settings.app_env == "development"
    )
    run_store = SqlAlchemyJobRunStore.from_engine(engine)
    audit_writer = SqlAlchemyAuditWriter.from_engine(engine)
    if not settings.ifind_enabled:
        _record_provider_failure(
            normalized_date,
            "ifind",
            scope,
            ConfigurationError("iFinD import is disabled"),
            run_store=run_store,
            audit_writer=audit_writer,
        )
    if scope == "full" and not settings.ifind_full_enabled:
        _record_provider_failure(
            normalized_date,
            "ifind",
            scope,
            ConfigurationError("iFinD full import is disabled pending pilot acceptance"),
            run_store=run_store,
            audit_writer=audit_writer,
        )
    if IFindHttpClient is None or IFindRawArchive is None:
        _record_provider_failure(
            normalized_date,
            "ifind",
            scope,
            DependencyError("iFinD HTTP dependencies are not installed"),
            run_store=run_store,
            audit_writer=audit_writer,
        )
    data_root = os.environ.get("DATA_ROOT", "data")
    archive = IFindRawArchive(data_root, secret_values=(settings.ifind_refresh_token,))
    client = IFindHttpClient(
        settings.ifind_refresh_token,
        base_url=settings.ifind_base_url,
        max_codes_per_request=settings.ifind_max_codes_per_request,
        max_concurrency=settings.ifind_max_concurrency,
        raw_archive=archive,
    )
    service = IFindIngestionService(
        client,
        repository,
        artifact_root=data_root,
        mapping_version="v1",
    )
    try:
        return import_ifind_data(
            normalized_date,
            service.import_business_date,
            scope=scope,
            run_store=run_store,
            audit_writer=audit_writer,
        )
    finally:
        client.close()


def import_tushare_data(
    business_date: date,
    service: Callable[..., Any] | Any,
    *,
    scope: str,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    """Run one publishable Tushare import with its own stable key namespace."""
    if scope not in {"pilot", "full"}:
        raise ValueError("scope must be pilot or full")
    operation = getattr(service, "import_business_date", service)
    return _run(
        "data-import",
        business_date,
        operation,
        scope=f"tushare-{scope}",
        phase="provider-import",
        service_kwargs={"scope": scope},
        run_store=run_store,
        audit_writer=audit_writer,
    )


def run_tushare_import(business_date: date | str, scope: str) -> JobResult:
    """RQ entry point. Secrets are read only from Settings, never job arguments."""
    if scope not in {"pilot", "full"}:
        raise ValueError("scope must be pilot or full")
    from app.application.tushare_ingestion import TushareIngestionService
    from app.core.config import ConfigurationError, load_settings
    from app.infrastructure.akshare import AKShareValidationClient
    from app.infrastructure.db.session import make_engine
    from app.infrastructure.providers import RawResponseArchive
    from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
    from app.infrastructure.repositories.runtime import SqlAlchemyAuditWriter, SqlAlchemyJobRunStore
    from app.infrastructure.tushare import TushareHttpClient

    normalized_date = date.fromisoformat(str(business_date)[:10])
    settings = load_settings()
    engine = make_engine(settings.database_url)
    repository = SqlAlchemyResearchRepository.from_engine(
        engine, create_schema=settings.app_env == "development"
    )
    run_store, audit_writer = (
        SqlAlchemyJobRunStore.from_engine(engine),
        SqlAlchemyAuditWriter.from_engine(engine),
    )
    if not settings.tushare_enabled:
        _record_provider_failure(
            normalized_date,
            "tushare",
            scope,
            ConfigurationError("Tushare import is disabled"),
            run_store=run_store,
            audit_writer=audit_writer,
        )
    if scope == "full" and not settings.tushare_full_enabled:
        _record_provider_failure(
            normalized_date,
            "tushare",
            scope,
            ConfigurationError("Tushare full import is disabled pending pilot acceptance"),
            run_store=run_store,
            audit_writer=audit_writer,
        )
    data_root = os.environ.get("DATA_ROOT", "data")
    archive = RawResponseArchive(data_root, secret_values=(settings.tushare_token,))
    client = TushareHttpClient(
        settings.tushare_token,
        timeout_seconds=settings.tushare_timeout_seconds,
        max_codes_per_request=settings.tushare_max_codes_per_request,
        max_concurrency=settings.tushare_max_concurrency,
        rate_limit_per_minute=settings.tushare_rate_limit_per_minute,
        raw_archive=archive,
    )
    validator = (
        AKShareValidationClient(raw_archive=archive)
        if settings.akshare_validation_enabled
        else None
    )
    service = TushareIngestionService(
        client,
        repository,
        validation_client=validator,
        artifact_root=data_root,
        mapping_version="v1",
        price_tolerance=settings.akshare_price_relative_tolerance,
        volume_tolerance=settings.akshare_volume_relative_tolerance,
        amount_tolerance=settings.akshare_amount_relative_tolerance,
        pilot_codes=settings.tushare_pilot_codes,
    )
    try:
        return import_tushare_data(
            normalized_date,
            service,
            scope=scope,
            run_store=run_store,
            audit_writer=audit_writer,
        )
    finally:
        client.close()


def validate_data_quality(
    business_date: date,
    service: Callable[..., Any],
    *,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run(
        "data-quality", business_date, service, run_store=run_store, audit_writer=audit_writer
    )


def run_daily_report(
    business_date: date,
    service: Callable[..., Any],
    *,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run(
        "daily-report", business_date, service, run_store=run_store, audit_writer=audit_writer
    )


def run_backtest(
    business_date: date,
    service: Callable[..., Any],
    *,
    scope: str,
    run_store: JobRunStore | None = None,
    audit_writer: AuditWriter | None = None,
) -> JobResult:
    return _run(
        "backtest",
        business_date,
        service,
        scope=scope,
        run_store=run_store,
        audit_writer=audit_writer,
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
        "backup",
        business_date,
        service,
        scope=scope,
        run_store=run_store,
        audit_writer=audit_writer,
    )


__all__ = [
    "import_ifind_data",
    "import_tushare_data",
    "import_authorized_data",
    "run_backtest",
    "run_backup",
    "run_daily_report",
    "run_idempotent_task",
    "run_ifind_import",
    "run_tushare_import",
    "update_calendar",
    "validate_data_quality",
]
