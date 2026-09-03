"""FastAPI application factory for the local validation runtime."""

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.base import RequestResponseEndpoint

from app.api import auth, health
from app.api.errors import error_response, register_error_handlers
from app.api.v1 import admin, backtests, data, operations, strategies
from app.api.v1.common import InMemoryResearchRepository
from app.core.config import Settings, load_settings
from app.core.contracts import (
    AuditWriter,
    IdempotencyRequest,
    IdempotencyResult,
    IdempotencyStore,
    InMemoryAuditWriter,
    InMemoryIdempotencyStore,
)
from app.core.dependencies import require_idempotency_key
from app.core.errors import ApplicationError
from app.core.logging import configure_logging
from app.core.security import (
    InMemorySessionRegistry,
    LocalAccount,
    LocalAuthenticator,
    PasswordHasher,
    TokenService,
)

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
_REPLAY_UNSAFE_HEADERS = {
    "connection",
    "content-length",
    "content-type",
    "date",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "server",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "x-request-id",
}


def _safe_replay_headers(headers: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, value)
        for name, value in headers.items()
        if name.lower() not in _REPLAY_UNSAFE_HEADERS
    )


def _with_current_request_id(body: bytes, content_type: str | None, request_id: str) -> bytes:
    if content_type is None or "application/json" not in content_type.lower():
        return body
    try:
        payload = json.loads(body)
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(payload, dict) or "request_id" not in payload:
        return body
    payload["request_id"] = request_id
    return json.dumps(payload, separators=(",", ":")).encode()


def create_app(
    *,
    settings: Settings | None = None,
    readiness_checks: Mapping[str, Callable[[], bool]] | None = None,
    accounts: Mapping[str, LocalAccount] | None = None,
    audit_writer: AuditWriter | None = None,
    idempotency_store: IdempotencyStore | None = None,
    repository: InMemoryResearchRepository | None = None,
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
    app.state.idempotency_store = idempotency_store or InMemoryIdempotencyStore()
    app.state.repository = repository or InMemoryResearchRepository()
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
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        supplied_request_id = request.headers.get("X-Request-Id", "")
        request_id = (
            supplied_request_id if _REQUEST_ID.fullmatch(supplied_request_id) else f"req_{uuid4().hex}"
        )
        request.state.request_id = request_id
        try:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                idempotency_key = require_idempotency_key(request.headers.get("Idempotency-Key"))
                request.state.idempotency_key = idempotency_key
                body = await request.body()
                actor_id = "anonymous"
                authorization = request.headers.get("Authorization", "")
                if authorization.startswith("Bearer "):
                    try:
                        actor_id = str(
                            app.state.authenticator.authenticate_access_token(
                                authorization.removeprefix("Bearer ")
                            ).user_id
                        )
                    except Exception:
                        actor_id = "anonymous"
                idempotency_request = IdempotencyRequest(
                    key=idempotency_key,
                    request_hash=sha256(
                        b"\0".join(
                            [request.method.encode(), request.url.path.encode(), body, actor_id.encode()]
                        )
                    ).hexdigest(),
                    actor_id=actor_id,
                    path=request.url.path,
                    expires_at=datetime.now(UTC) + timedelta(days=7),
                )
                replay = app.state.idempotency_store.acquire(idempotency_request)
                if replay is not None:
                    replay_body = _with_current_request_id(
                        replay.body, replay.content_type, request_id
                    )
                    response = Response(
                        content=replay_body,
                        status_code=replay.status_code,
                        media_type=replay.content_type,
                        headers=dict(replay.headers),
                    )
                    response.headers["X-Request-Id"] = request_id
                    return response
        except ApplicationError as exc:
            response = error_response(request, exc.status_code, exc.code, exc.message, exc.details)
        else:
            try:
                response = await call_next(request)
            except Exception:
                response = error_response(
                    request, 500, "INTERNAL_ERROR", "An unexpected error occurred"
                )
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                streaming_response: Any = response
                response_body = b"".join(
                    [chunk async for chunk in streaming_response.body_iterator]
                )
                content_type = response.media_type or response.headers.get("content-type")
                response_body = _with_current_request_id(
                    response_body, content_type, request_id
                )
                response_headers = _safe_replay_headers(response.headers)
                response = Response(
                    content=response_body,
                    status_code=response.status_code,
                    media_type=content_type,
                    headers=dict(response_headers),
                )
                app.state.idempotency_store.complete(
                    idempotency_request,
                    IdempotencyResult(
                        response.status_code,
                        response_body,
                        content_type,
                        response_headers,
                    ),
                )
        response.headers["X-Request-Id"] = request_id
        return response

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(data.router)
    app.include_router(strategies.router)
    app.include_router(backtests.router)
    app.include_router(operations.router)
    app.include_router(admin.router)
    return app
