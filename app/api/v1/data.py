from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit, repository
from app.core.dependencies import require_roles
from app.core.security import Principal, Role
from app.schemas.data import BatchCreate

router = APIRouter(prefix="/api/v1", tags=["data"])
User = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]
AnyUser = Annotated[Principal, Depends(require_roles(Role.USER, Role.REVIEWER, Role.ADMIN))]


@router.post("/data/batches", status_code=201)
def create_batch(payload: BatchCreate, request: Request, principal: User) -> JSONResponse:
    record = repository(request).create_batch(principal.user_id, payload.model_dump(mode="json"))
    audit(request, principal, "DATA_BATCH_CREATE", "data_batch", record["batch_id"], "SUCCESS")
    return _success(request, record, 201)


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
    _: AnyUser,
    trade_date: date = Query(...),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: str | None = None,
) -> JSONResponse:
    return _success(request, repository(request).list_pool(trade_date, status, page, page_size))
