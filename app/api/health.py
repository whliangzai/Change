"""Dependency-neutral liveness and non-sensitive readiness endpoints."""

from collections.abc import Callable, Mapping

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

ReadinessCheck = Callable[[], bool]
router = APIRouter(tags=["health"])


def _success(request: Request, data: Mapping[str, object], status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"data": data, "request_id": request.state.request_id},
    )


@router.get("/health")
def health(request: Request) -> JSONResponse:
    return _success(request, {"status": "ok"})


@router.get("/api/v1/health/readiness")
def readiness(request: Request) -> JSONResponse:
    checks: Mapping[str, ReadinessCheck] = request.app.state.readiness_checks
    outcomes: dict[str, str] = {}
    for name, check in checks.items():
        try:
            outcomes[name] = "ok" if check() else "unavailable"
        except Exception:  # readiness must not disclose dependency details
            outcomes[name] = "unavailable"
    ready = all(value == "ok" for value in outcomes.values())
    return _success(
        request,
        {"status": "ready" if ready else "not_ready", "checks": outcomes},
        200 if ready else 503,
    )
