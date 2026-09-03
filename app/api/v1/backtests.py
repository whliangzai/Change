from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit, repository
from app.core.dependencies import require_roles
from app.core.errors import ApplicationError, DataUnavailableError
from app.core.security import Principal, Role
from app.schemas.backtest import BacktestCreate

router = APIRouter(prefix="/api/v1", tags=["backtests"])
User = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]


@router.post("/backtests", status_code=202)
def create_backtest(payload: BacktestCreate, request: Request, principal: User) -> JSONResponse:
    data = payload.model_dump(mode="json")
    if not repository(request).dependencies_available(data):
        raise DataUnavailableError(
            "backtest prerequisites are unavailable",
            [{"field": field, "reason": "missing or unavailable"} for field in ("data_batch_id", "cost_config_id", "rule_config_id")],
        )
    record = repository(request).create_run(principal.user_id, data)
    audit(request, principal, "BACKTEST_CREATE", "backtest_run", record["run_id"], "SUCCESS")
    return _success(request, record, 202)


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
