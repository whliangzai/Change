from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit, repository
from app.core.dependencies import require_roles
from app.core.errors import ApplicationError
from app.core.security import Principal, Role
from app.schemas.operations import ExecutionCreate, PlanDecision

router = APIRouter(prefix="/api/v1", tags=["operations"])
User = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]
Reader = Annotated[Principal, Depends(require_roles(Role.USER, Role.REVIEWER, Role.ADMIN))]
Reviewer = Annotated[Principal, Depends(require_roles(Role.REVIEWER, Role.ADMIN))]


@router.get("/daily-reports/{report_date}")
def daily_report(report_date: date, request: Request, _: Reader) -> JSONResponse:
    return _success(request, repository(request).daily_report(report_date))


@router.get("/order-plans")
def order_plans(
    request: Request,
    _: Reader,
    execution_date: date = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    return _success(request, repository(request).list_plans(execution_date, page, page_size))


@router.post("/order-plans/{plan_id}/confirm")
def confirm_plan(plan_id: str, payload: PlanDecision, request: Request, principal: Reviewer) -> JSONResponse:
    record = repository(request).confirm_plan(plan_id, principal.user_id, payload.decision, payload.expected_version, payload.review_note)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    audit(request, principal, "ORDER_PLAN_" + payload.decision, "order_plan", plan_id, "SUCCESS")
    return _success(request, record)


@router.post("/executions", status_code=201)
def create_execution(payload: ExecutionCreate, request: Request, principal: User) -> JSONResponse:
    record = repository(request).create_execution(payload.model_dump(mode="json"), principal.user_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    audit(request, principal, "EXECUTION_MANUAL_ENTRY", "execution_record", record["execution_id"], "SUCCESS")
    return _success(request, record, 201)


@router.get("/accounts/{account_id}/snapshots")
def snapshots(
    account_id: str, request: Request, _: Reader,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    return _success(request, repository(request).snapshots(account_id, page, page_size))


@router.get("/reports/{report_id}/export", status_code=202)
def export_report(report_id: str, request: Request, principal: User) -> JSONResponse:
    record = repository(request).create_export(report_id, principal.user_id)
    audit(request, principal, "REPORT_EXPORT", "report", report_id, "SUCCESS")
    return _success(request, record, 202)


@router.get("/exports/{export_id}")
def get_export(export_id: str, request: Request, _: Reader) -> JSONResponse:
    record = repository(request).get_export(export_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, record)
