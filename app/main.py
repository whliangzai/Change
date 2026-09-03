"""FastAPI application factory for the local validation runtime."""

from collections.abc import Callable, Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.api import audit, auth, health
from app.api.errors import error_response, register_error_handlers
from app.core.config import Settings, load_settings
from app.core.contracts import AuditWriter, InMemoryAuditWriter
from app.core.dependencies import require_idempotency_key
from app.core.errors import ValidationError
from app.core.logging import configure_logging
from app.core.security import (
    InMemorySessionRegistry,
    LocalAccount,
    LocalAuthenticator,
    PasswordHasher,
    TokenService,
)


def create_app(
    *,
    settings: Settings | None = None,
    readiness_checks: Mapping[str, Callable[[], bool]] | None = None,
    accounts: Mapping[str, LocalAccount] | None = None,
    audit_writer: AuditWriter | None = None,
) -> FastAPI:
    """Build an application with replaceable process-local adapters for testing."""
    runtime_settings = settings or load_settings()
    app = FastAPI(title="A-share Quantitative Validation", version="0.1.0")
    app.state.settings = runtime_settings
    app.state.readiness_checks = readiness_checks or {
        "database": lambda: True,
        "queue": lambda: True,
    }
    app.state.audit_writer = audit_writer or InMemoryAuditWriter()
    app.state.authenticator = LocalAuthenticator(
        accounts=dict(accounts or {}),
        password_hasher=PasswordHasher(),
        token_service=TokenService(runtime_settings.auth_secret_key),
        sessions=InMemorySessionRegistry(),
    )
    configure_logging()
    register_error_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Response]
    ) -> Response:
        request_id = request.headers.get("X-Request-Id") or f"req_{uuid4().hex}"
        request.state.request_id = request_id
        try:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                require_idempotency_key(request.headers.get("Idempotency-Key"))
        except ValidationError as exc:
            response = error_response(request, exc.status_code, exc.code, exc.message, exc.details)
        else:
            response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(audit.router)
    return app


app = create_app()
