from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit, repository
from app.core.dependencies import require_roles
from app.core.errors import ApplicationError, DataUnavailableError, DependencyError
from app.core.security import Principal, Role
from app.schemas.backtest import BacktestCreate

router = APIRouter(prefix="/api/v1", tags=["backtests"])
User = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]


@router.post("/backtests", status_code=202)
def create_backtest(payload: BacktestCreate, request: Request, principal: User) -> JSONResponse:
    data = payload.model_dump(mode="json")
    data["owner_id"] = str(principal.user_id)
    store = repository(request)
    validator = getattr(store, "validate_backtest", None)
    validation = validator(principal.user_id, data) if callable(validator) else None
    if validation is not None and not validation["available"]:
        raise DataUnavailableError(
            str(validation["reason"]),
            [{"field": "backtest", "reason": str(validation["reason"])}],
        )
    if validation is None and not store.dependencies_available(data):
        raise DataUnavailableError(
            "backtest prerequisites are unavailable",
            [
                {"field": field, "reason": "missing or unavailable"}
                for field in ("data_batch_id", "cost_config_id", "rule_config_id")
            ],
        )
    record = store.create_run(principal.user_id, data)
    audit(request, principal, "BACKTEST_CREATE", "backtest_run", record["run_id"], "SUCCESS")
    return _success(request, record, 202)


@router.get("/backtests")
def list_backtests(
    request: Request,
    principal: User,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = None,
) -> JSONResponse:
    return _success(
        request,
        repository(request).list_backtests(principal.user_id, status, page, page_size),
    )


@router.get("/backtests/{run_id}")
def get_backtest(run_id: str, request: Request, principal: User) -> JSONResponse:
    record = repository(request).get_run(run_id, principal.user_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, record)


@router.get("/backtests/{run_id}/trades")
def get_trades(run_id: str, request: Request, principal: User) -> JSONResponse:
    record = repository(request).get_run_child(run_id, principal.user_id, "trades")
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, record)


@router.get("/backtests/{run_id}/report")
def get_report(run_id: str, request: Request, principal: User) -> JSONResponse:
    record = repository(request).get_run_child(run_id, principal.user_id, "report")
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, record)


@router.post("/backtests/{run_id}/execute", status_code=202)
def execute_backtest(run_id: str, request: Request, principal: User) -> JSONResponse:
    executor = getattr(repository(request), "execute_run", None)
    if executor is None:
        raise DependencyError("backtest execution service is not configured")
    record = executor(run_id, principal.user_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    audit_result = "SUCCESS" if record["status"] == "SUCCEEDED" else "UNAVAILABLE"
    audit(request, principal, "BACKTEST_EXECUTE", "backtest_run", run_id, audit_result)
    return _success(request, record, 202)
