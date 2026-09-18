from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit
from app.core.dependencies import get_audit_writer, require_roles
from app.core.errors import ApplicationError, DependencyError, StateConflictError
from app.core.security import Principal, Role
from app.jobs.idempotency import (
    JobRunRecord,
    provider_from_task_key,
    retryable_error_summary,
    scope_from_task_key,
)
from app.jobs.queue import enqueue
from app.jobs.tasks import run_ifind_import, run_tushare_import

router = APIRouter(prefix="/api/v1", tags=["admin"])
Reviewer = Annotated[Principal, Depends(require_roles(Role.REVIEWER, Role.ADMIN))]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]


def _queue_available(request: Request) -> bool:
    check = request.app.state.readiness_checks.get("queue")
    if check is None:
        return False
    try:
        return bool(check())
    except Exception:
        return False


def _provider_capability(request: Request, provider: str) -> dict[str, object]:
    settings = request.app.state.settings
    enabled = bool(getattr(settings, f"{provider}_enabled", False))
    full_enabled = bool(getattr(settings, f"{provider}_full_enabled", False))
    return {
        "enabled": enabled,
        "full_enabled": full_enabled,
        "full_open": enabled and full_enabled,
        "full_reason": None
        if enabled and full_enabled
        else (
            f"{provider} 未启用"
            if not enabled
            else "full 未开放；请先完成 pilot 审核并显式启用门禁"
        ),
    }


def _ensure_provider_allowed(request: Request, provider: str, scope: str) -> None:
    capability = _provider_capability(request, provider)
    if not capability["enabled"]:
        raise DependencyError(f"{provider} import is disabled")
    if scope == "full" and not capability["full_open"]:
        raise DependencyError(f"{provider} full import is disabled pending pilot acceptance")


def _safe_error(summary: str | None, request: Request) -> str | None:
    if summary is None:
        return None
    redacted = summary
    for secret in (
        getattr(request.app.state.settings, "tushare_token", ""),
        getattr(request.app.state.settings, "ifind_refresh_token", ""),
    ):
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def _job_payload(record: JobRunRecord, request: Request) -> dict[str, object]:
    value = record.value if isinstance(record.value, dict) else {}
    provider = provider_from_task_key(record.idempotency_key)
    scope = scope_from_task_key(record.idempotency_key)
    error = _safe_error(record.error_summary, request)
    ended_at = record.finished_at.isoformat() if record.finished_at else None
    stale_queued = _queued_is_stale(record, request)
    return {
        "job_id": record.run_id,
        "id": record.run_id,
        "task_key": record.idempotency_key,
        "job_kind": record.job_kind,
        "job_type": record.job_kind,
        "provider": provider,
        "scope": scope,
        "business_date": record.business_date.isoformat(),
        "status": record.status.upper(),
        "phase": record.phase,
        "attempt": record.attempt,
        "run_number": record.run_number,
        "value": record.value,
        "error_summary": error,
        "error": error,
        "failure_reason": error,
        "retryable": record.status == "failed" and retryable_error_summary(record.error_summary),
        "requeueable": stale_queued,
        "queued_stale": stale_queued,
        "started_at": record.started_at.isoformat(),
        "ended_at": ended_at,
        "completed_at": ended_at,
        "batch_id": value.get("batch_id"),
        "quality_status": value.get("quality_status"),
        "quality_summary": value.get("quality_summary", {}),
    }


def _queued_is_stale(record: JobRunRecord, request: Request) -> bool:
    if record.status != "queued":
        return False
    started_at = record.started_at
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - started_at >= timedelta(
        seconds=request.app.state.settings.job_queue_stale_after_seconds
    )


def _audit_job(
    request: Request,
    principal: Principal,
    action: str,
    record: JobRunRecord,
    result: str,
) -> None:
    audit(
        request,
        principal,
        action,
        "job_run",
        record.run_id,
        result,
        after_summary={
            "task_key": record.idempotency_key,
            "job_id": record.run_id,
            "request_id": request.state.request_id,
            "request_idempotency_key": getattr(request.state, "idempotency_key", None),
            "status": record.status.upper(),
        },
    )


def _mark_queue_failed(
    request: Request, principal: Principal, record: JobRunRecord, exc: BaseException, action: str
) -> None:
    summary = f"DependencyError: {str(exc).replace(chr(10), ' ').strip()[:240]}"
    failed = replace(record, status="failed", error_summary=summary, finished_at=datetime.now(UTC))
    request.app.state.job_run_store.replace(failed)
    _audit_job(request, principal, action, failed, "FAILURE")


def _enqueue_provider(
    request: Request,
    principal: Principal,
    provider: Literal["ifind", "tushare"],
    business_date: date,
    scope: Literal["pilot", "full"],
    *,
    retry_failed: bool = False,
) -> tuple[JobRunRecord, bool]:
    _ensure_provider_allowed(request, provider, scope)
    task_key = f"data-import:{business_date.isoformat()}:{provider}-{scope}"
    store = request.app.state.job_run_store
    record, created = store.reserve_queued_owned(
        "data-import",
        business_date,
        task_key,
        phase="enqueueing",
        value={"provider": provider, "scope": scope},
        retry_failed=retry_failed,
    )
    if not created:
        return record, False
    function = run_ifind_import if provider == "ifind" else run_tushare_import
    enqueue_fn = getattr(request.app.state, f"{provider}_enqueue", enqueue)
    try:
        # RQ job IDs have a stricter charset than our human-readable task key.
        # Keep idempotency in the durable job_run row and use its UUID for RQ.
        enqueue_fn(function, business_date.isoformat(), scope, job_id=record.run_id)
    except DependencyError as dependency_exc:
        _mark_queue_failed(
            request, principal, record, dependency_exc, f"{provider.upper()}_IMPORT_QUEUE"
        )
        raise
    except Exception as exc:
        detail = str(exc).replace("\r", " ").replace("\n", " ").strip()[:160]
        dependency_error = DependencyError(
            f"Redis queue is unavailable ({type(exc).__name__}): {detail}"
        )
        _mark_queue_failed(
            request, principal, record, dependency_error, f"{provider.upper()}_IMPORT_QUEUE"
        )
        raise dependency_error from exc
    marked: JobRunRecord | None = store.mark_queued(record.run_id, phase="provider-import")
    return marked or record, True


def _requeue_provider(
    request: Request,
    principal: Admin,
    current: JobRunRecord,
    provider: Literal["ifind", "tushare"],
    scope: Literal["pilot", "full"],
) -> JobRunRecord:
    _ensure_provider_allowed(request, provider, scope)
    store = request.app.state.job_run_store
    record, claimed = store.claim_queued_for_requeue(
        current.run_id, expected_started_at=current.started_at
    )
    if not claimed:
        raise StateConflictError("This queued job is already being recovered or has started")
    function = run_ifind_import if provider == "ifind" else run_tushare_import
    enqueue_fn = getattr(request.app.state, f"{provider}_enqueue", enqueue)
    try:
        enqueue_fn(
            function, current.business_date.isoformat(), scope, job_id=current.run_id
        )
    except DependencyError as dependency_exc:
        _mark_queue_failed(
            request, principal, record, dependency_exc, f"{provider.upper()}_IMPORT_REQUEUE"
        )
        raise
    except Exception as exc:
        detail = str(exc).replace("\r", " ").replace("\n", " ").strip()[:160]
        dependency_error = DependencyError(
            f"Redis queue is unavailable ({type(exc).__name__}): {detail}"
        )
        _mark_queue_failed(
            request, principal, record, dependency_error, f"{provider.upper()}_IMPORT_REQUEUE"
        )
        raise dependency_error from exc
    marked: JobRunRecord | None = store.mark_queued(record.run_id, phase="provider-import")
    return marked or record


@router.get("/admin/data-imports/capabilities")
def import_capabilities(request: Request, _: Admin) -> JSONResponse:
    queue_available = _queue_available(request)
    tushare = _provider_capability(request, "tushare")
    ifind = _provider_capability(request, "ifind")
    return _success(
        request,
        {
            "providers": {"tushare": tushare, "ifind": ifind},
            "tushare": tushare,
            "ifind": ifind,
            "tushare_enabled": tushare["enabled"],
            "ifind_enabled": ifind["enabled"],
            "queue": {"available": queue_available},
            "queue_available": queue_available,
            "akshare": {"role": "validation_only", "can_replace_primary": False},
        },
    )


@router.get("/jobs")
def jobs(
    request: Request,
    _: Admin,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    provider: str | None = None,
    status: str | None = None,
    business_date: date | None = Query(None),
) -> JSONResponse:
    normalized_provider = provider.lower() if provider else None
    if normalized_provider and normalized_provider not in {"tushare", "ifind"}:
        raise ApplicationError("VALIDATION_ERROR", "provider must be tushare or ifind", 400)
    records, total = request.app.state.job_run_store.page(
        page, page_size, provider=normalized_provider, status=status, business_date=business_date
    )
    return _success(
        request,
        {
            "items": [_job_payload(record, request) for record in records],
            "page": page,
            "page_size": page_size,
            "total": total,
        },
    )


@router.get("/jobs/{job_id}")
def job_detail(job_id: str, request: Request, _: Admin) -> JSONResponse:
    record = request.app.state.job_run_store.get(job_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested job was not found", 404)
    return _success(request, _job_payload(record, request))


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request, principal: Admin) -> JSONResponse:
    current = request.app.state.job_run_store.get(job_id)
    if current is None:
        raise ApplicationError("NOT_FOUND", "Requested job was not found", 404)
    latest = request.app.state.job_run_store.latest(current.idempotency_key)
    if latest is None or latest.run_id != current.run_id:
        raise StateConflictError("The requested job attempt has already been superseded")
    provider = provider_from_task_key(current.idempotency_key)
    scope = scope_from_task_key(current.idempotency_key)
    if provider not in {"ifind", "tushare"} or scope not in {"pilot", "full"}:
        raise StateConflictError("This job does not have a supported provider retry path")
    if current.status == "queued":
        if not _queued_is_stale(current, request):
            raise StateConflictError(
                "This queued job is not stale yet; wait for the queue timeout before recovering it"
            )
        record = _requeue_provider(
            request,
            principal,
            current,
            provider,  # type: ignore[arg-type]
            scope,  # type: ignore[arg-type]
        )
        _audit_job(request, principal, "JOB_REQUEUE", record, "SUCCESS")
        return _success(request, _job_payload(record, request), 202)
    if current.status != "failed" or not retryable_error_summary(current.error_summary):
        raise StateConflictError("Only dependency failures can be retried")
    record, _ = _enqueue_provider(
        request,
        principal,
        provider,  # type: ignore[arg-type]
        current.business_date,
        scope,  # type: ignore[arg-type]
        retry_failed=True,
    )
    _audit_job(request, principal, "JOB_RETRY", record, "SUCCESS")
    return _success(request, _job_payload(record, request), 202)


@router.post("/admin/data-imports/ifind/{business_date}")
def enqueue_ifind_import(
    business_date: date,
    request: Request,
    principal: Admin,
    scope: Literal["pilot", "full"] = Query(...),
) -> JSONResponse:
    """Queue an auditable iFinD import behind the protected admin boundary."""
    record, _ = _enqueue_provider(request, principal, "ifind", business_date, scope)
    _audit_job(request, principal, "IFIND_IMPORT_QUEUE", record, "SUCCESS")
    return _success(request, _job_payload(record, request), 202)


@router.post("/admin/data-imports/tushare/{business_date}")
def enqueue_tushare_import(
    business_date: date,
    request: Request,
    principal: Admin,
    scope: Literal["pilot", "full"] = Query(...),
) -> JSONResponse:
    """Queue the primary Tushare supplier import behind the admin boundary."""
    record, _ = _enqueue_provider(request, principal, "tushare", business_date, scope)
    _audit_job(request, principal, "TUSHARE_IMPORT_QUEUE", record, "SUCCESS")
    return _success(request, _job_payload(record, request), 202)


@router.get("/audit-events")
def audit_events(
    request: Request,
    principal: Reviewer,
    audit_writer: Annotated[object, Depends(get_audit_writer)],
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    if hasattr(audit_writer, "page"):
        items, total = audit_writer.page(page, page_size)
        return _success(
            request, {"items": items, "page": page, "page_size": page_size, "total": total}
        )
    events = getattr(audit_writer, "events", [])
    start = (page - 1) * page_size
    items = [
        {
            "occurred_at": event.occurred_at.isoformat(),
            "actor_id": str(event.actor_id) if event.actor_id else None,
            "actor_roles": list(event.actor_roles),
            "action": event.action,
            "object_type": event.object_type,
            "object_id": event.object_id,
            "request_id": event.request_id,
            "result": event.result,
            "task_key": (
                event.after_summary.get("task_key")
                if event.after_summary
                else event.idempotency_key
            ),
            "job_id": event.after_summary.get("job_id") if event.after_summary else event.object_id,
        }
        for event in events[start : start + page_size]
    ]
    return _success(
        request, {"items": items, "page": page, "page_size": page_size, "total": len(events)}
    )
