from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit, repository
from app.core.dependencies import require_roles
from app.core.errors import DataUnavailableError, ValidationError
from app.core.security import Principal, Role
from app.schemas.data import BatchCreate, BatchImport

router = APIRouter(prefix="/api/v1", tags=["data"])
User = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]
Admin = Annotated[Principal, Depends(require_roles(Role.ADMIN))]
AnyUser = Annotated[Principal, Depends(require_roles(Role.USER, Role.REVIEWER, Role.ADMIN))]


@router.post("/data/batches", status_code=201)
def create_batch(payload: BatchCreate, request: Request, principal: User) -> JSONResponse:
    record = repository(request).create_batch(principal.user_id, payload.model_dump(mode="json"))
    audit(request, principal, "DATA_BATCH_CREATE", "data_batch", record["batch_id"], "SUCCESS")
    return _success(request, record, 201)


@router.post("/data/imports", status_code=201)
def import_batch(payload: BatchImport, request: Request, principal: Admin) -> JSONResponse:
    try:
        record = request.app.state.data_import_service.import_file(
            principal.user_id, payload.model_dump(mode="json")
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc
    if record["quality_status"] not in {"AVAILABLE", "WARNING_AVAILABLE"}:
        audit(request, principal, "DATA_IMPORT", "data_batch", record["batch_id"], "BLOCKED")
        raise DataUnavailableError(
            "market-data import was blocked by the quality gate",
            [{"batch_id": record["batch_id"], "quality": record["quality_summary"]}],
        )
    audit(request, principal, "DATA_IMPORT", "data_batch", record["batch_id"], "SUCCESS")
    return _success(request, record, 201)


@router.get("/data/batches")
def list_batches(
    request: Request,
    principal: AnyUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = None,
) -> JSONResponse:
    return _success(
        request, repository(request).list_batches(principal.user_id, status, page, page_size)
    )


@router.get("/data/batches/{batch_id}/quality")
def batch_quality(batch_id: str, request: Request, principal: AnyUser) -> JSONResponse:
    record = repository(request).get_batch_quality(batch_id, principal.user_id)
    if record is None:
        from app.core.errors import ApplicationError

        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, record)


@router.get("/securities/pool")
def security_pool(
    request: Request,
    principal: AnyUser,
    trade_date: date = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = None,
) -> JSONResponse:
    return _success(
        request,
        repository(request).list_pool(trade_date, status, page, page_size, principal.user_id),
    )


@router.get("/data/bars")
def daily_bars(
    request: Request,
    principal: AnyUser,
    trade_date: date = Query(...),
    symbol: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    return _success(
        request,
        repository(request).list_daily_bars(trade_date, symbol, page, page_size, principal.user_id),
    )
