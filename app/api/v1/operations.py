from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit, repository
from app.core.dependencies import require_roles
from app.core.errors import ApplicationError, DependencyError
from app.core.security import Principal, Role
from app.schemas.daily_flow import DailyFlowCreate
from app.schemas.operations import ExecutionCreate, PlanDecision

router = APIRouter(prefix="/api/v1", tags=["operations"])
User = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]
Reader = Annotated[Principal, Depends(require_roles(Role.USER, Role.REVIEWER, Role.ADMIN))]
Reviewer = Annotated[Principal, Depends(require_roles(Role.REVIEWER, Role.ADMIN))]


@router.post("/daily-flows", status_code=202)
def run_daily_flow(payload: DailyFlowCreate, request: Request, principal: Admin) -> JSONResponse:
    runner = getattr(repository(request), "run_daily_flow", None)
    if runner is None:
        raise DependencyError("daily flow service is not configured")
    data = payload.model_dump(mode="json")
    data["idempotency_key"] = request.state.idempotency_key
    record = runner(principal.user_id, data)
    audit(request, principal, "DAILY_FLOW_RUN", "daily_flow_run", record["run_id"], "SUCCESS")
    return _success(request, record, 202)


@router.get("/daily-reports/{report_date}")
def daily_report(report_date: date, request: Request, principal: Reader) -> JSONResponse:
    return _success(request, repository(request).daily_report(report_date, principal.user_id))


@router.get("/order-plans")
def order_plans(
    request: Request,
    principal: Reader,
    execution_date: date = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    return _success(
        request, repository(request).list_plans(execution_date, page, page_size, principal.user_id)
    )


@router.post("/order-plans/{plan_id}/confirm")
def confirm_plan(
    plan_id: str, payload: PlanDecision, request: Request, principal: Reviewer
) -> JSONResponse:
    record = repository(request).confirm_plan(
        plan_id, principal.user_id, payload.decision, payload.expected_version, payload.review_note
    )
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    audit(request, principal, "ORDER_PLAN_" + payload.decision, "order_plan", plan_id, "SUCCESS")
    return _success(request, record)


@router.post("/executions", status_code=201)
def create_execution(payload: ExecutionCreate, request: Request, principal: User) -> JSONResponse:
    data = payload.model_dump(mode="json")
    data["idempotency_key"] = request.state.idempotency_key
    record = repository(request).create_execution(data, principal.user_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    audit(
        request,
        principal,
        "EXECUTION_MANUAL_ENTRY",
        "execution_record",
        record["execution_id"],
        "SUCCESS",
    )
    return _success(request, record, 201)


@router.get("/accounts/{account_id}/snapshots")
def snapshots(
    account_id: str,
    request: Request,
    principal: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    if account_id != str(principal.user_id):
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, repository(request).snapshots(account_id, page, page_size))


@router.get("/reports/{report_id}/export", status_code=202)
def export_report(report_id: str, request: Request, principal: User) -> JSONResponse:
    record = repository(request).create_export(report_id, principal.user_id)
    audit(request, principal, "REPORT_EXPORT", "report", report_id, "SUCCESS")
    return _success(request, record, 202)


@router.get("/exports/{export_id}")
def get_export(export_id: str, request: Request, principal: Reader) -> JSONResponse:
    record = repository(request).get_export(export_id, principal.user_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, record)
