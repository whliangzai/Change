"""HTTP serialization for domain and authentication errors."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import ApplicationError
from app.core.security import AccessDeniedError, AuthenticationError


def error_response(
    request: Request, status_code: int, code: str, message: str, details: list[object] | None = None
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message, "details": details or []},
            "request_id": request_id,
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApplicationError)
    async def application_error_handler(request: Request, exc: ApplicationError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(
        request: Request, exc: AuthenticationError
    ) -> JSONResponse:
        return error_response(request, 401, "AUTH_REQUIRED", str(exc))

    @app.exception_handler(AccessDeniedError)
    async def access_denied_handler(request: Request, exc: AccessDeniedError) -> JSONResponse:
        return error_response(request, 403, "FORBIDDEN", str(exc))
