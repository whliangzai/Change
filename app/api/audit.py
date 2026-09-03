"""Restricted audit query placeholder until the database adapter is added."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.health import _success
from app.core.dependencies import require_roles
from app.core.security import Principal, Role

router = APIRouter(prefix="/api/v1", tags=["audit"])
ReviewOrAdmin = Annotated[Principal, Depends(require_roles(Role.REVIEWER, Role.ADMIN))]


@router.get("/audit-events")
def list_audit_events(
    request: Request,
    _: ReviewOrAdmin,
) -> JSONResponse:
    """Expose the route boundary without making in-memory events a data store."""
    return _success(request, {"items": [], "page": 1, "page_size": 50, "total": 0})
