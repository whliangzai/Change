from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.api.v1.common import audit, repository
from app.core.dependencies import require_roles
from app.core.errors import ApplicationError
from app.core.security import Principal, Role
from app.schemas.strategy import StrategyCreate, StrategyReview

router = APIRouter(prefix="/api/v1", tags=["strategies"])
User = Annotated[Principal, Depends(require_roles(Role.USER, Role.ADMIN))]
Reader = Annotated[Principal, Depends(require_roles(Role.USER, Role.REVIEWER, Role.ADMIN))]
Reviewer = Annotated[Principal, Depends(require_roles(Role.REVIEWER, Role.ADMIN))]


@router.post("/strategies", status_code=201)
def create_strategy(payload: StrategyCreate, request: Request, principal: User) -> JSONResponse:
    record = repository(request).create_strategy(principal.user_id, payload.model_dump(mode="json"))
    audit(
        request,
        principal,
        "STRATEGY_CREATE",
        "strategy_version",
        record["strategy_version_id"],
        "SUCCESS",
    )
    return _success(request, record, 201)


@router.post("/strategies/{strategy_id}/submit-review")
def submit_review(
    strategy_id: str, payload: StrategyReview, request: Request, principal: Reviewer
) -> JSONResponse:
    record = repository(request).submit_strategy(
        strategy_id, principal.user_id, payload.decision, payload.review_note, allow_reviewer=True
    )
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    audit(request, principal, "STRATEGY_REVIEW", "strategy_version", strategy_id, "SUCCESS")
    return _success(request, record)


@router.get("/strategies/{strategy_id}/diff")
def strategy_diff(strategy_id: str, request: Request, principal: Reader) -> JSONResponse:
    record = repository(request).strategy_diff(strategy_id, principal.user_id)
    if record is None:
        raise ApplicationError("NOT_FOUND", "Requested resource was not found", 404)
    return _success(request, record)
