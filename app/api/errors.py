"""HTTP serialization for domain and authentication errors."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
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

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {"field": ".".join(str(item) for item in error["loc"]), "reason": error["msg"]}
            for error in exc.errors()
        ]
        return error_response(request, 400, "VALIDATION_ERROR", "Request validation failed", details)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = {
            400: ("VALIDATION_ERROR", "Request validation failed"),
            401: ("AUTH_REQUIRED", "Authentication is required"),
            403: ("FORBIDDEN", "Access is forbidden"),
            404: ("NOT_FOUND", "Requested resource was not found"),
            409: ("STATE_CONFLICT", "Requested state transition is not allowed"),
        }.get(exc.status_code, ("INTERNAL_ERROR", "Request could not be completed"))
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _: Exception) -> JSONResponse:
        return error_response(request, 500, "INTERNAL_ERROR", "An unexpected error occurred")
