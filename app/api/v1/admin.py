from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit
from app.core.dependencies import get_audit_writer, require_roles
from app.core.errors import DependencyError
from app.core.security import Principal, Role
from app.jobs.queue import enqueue
from app.jobs.tasks import run_ifind_import, run_tushare_import

router = APIRouter(prefix="/api/v1", tags=["admin"])
Reviewer = Annotated[Principal, Depends(require_roles(Role.REVIEWER, Role.ADMIN))]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]


@router.get("/jobs")
def jobs(
    request: Request, _: Admin, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200)
) -> JSONResponse:
    return _success(request, {"items": [], "page": page, "page_size": page_size, "total": 0})


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, request: Request, principal: Admin) -> JSONResponse:
    audit(request, principal, "JOB_RETRY", "job_run", job_id, "SUCCESS")
    return _success(request, {"job_id": job_id, "status": "QUEUED"}, 202)


@router.post("/admin/data-imports/ifind/{business_date}")
def enqueue_ifind_import(
    business_date: date,
    request: Request,
    principal: Admin,
    scope: Literal["pilot", "full"] = Query(...),
) -> JSONResponse:
    """Queue an auditable iFinD import behind the protected admin boundary."""
    settings = request.app.state.settings
    if not settings.ifind_enabled:
        raise DependencyError("iFinD import is disabled")

    task_key = f"data-import:{business_date.isoformat()}:ifind-{scope}"
    enqueue_fn = getattr(request.app.state, "ifind_enqueue", enqueue)
    job = enqueue_fn(run_ifind_import, business_date.isoformat(), scope, job_id=task_key)
    job_id = str(getattr(job, "id", None) or task_key)
    audit(request, principal, "IFIND_IMPORT_QUEUE", "job_run", task_key, "SUCCESS")
    return _success(
        request,
        {
            "job_id": job_id,
            "task_key": task_key,
            "business_date": business_date.isoformat(),
            "scope": scope,
        },
        202,
    )


@router.post("/admin/data-imports/tushare/{business_date}")
def enqueue_tushare_import(
    business_date: date,
    request: Request,
    principal: Admin,
    scope: Literal["pilot", "full"] = Query(...),
) -> JSONResponse:
    """Queue the primary Tushare supplier import behind the admin boundary."""
    if not request.app.state.settings.tushare_enabled:
        raise DependencyError("Tushare import is disabled")
    if scope == "full" and not request.app.state.settings.tushare_full_enabled:
        raise DependencyError("Tushare full import is disabled pending pilot acceptance")
    task_key = f"data-import:{business_date.isoformat()}:tushare-{scope}"
    enqueue_fn = getattr(request.app.state, "tushare_enqueue", enqueue)
    job = enqueue_fn(run_tushare_import, business_date.isoformat(), scope, job_id=task_key)
    job_id = str(getattr(job, "id", None) or task_key)
    audit(request, principal, "TUSHARE_IMPORT_QUEUE", "job_run", task_key, "SUCCESS")
    return _success(
        request,
        {
            "job_id": job_id,
            "task_key": task_key,
            "business_date": business_date.isoformat(),
            "scope": scope,
        },
        202,
    )


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
        }
        for event in events[start : start + page_size]
    ]
    return _success(
        request, {"items": items, "page": page, "page_size": page_size, "total": len(events)}
    )
