"""FastAPI application factory for the local validation runtime."""

import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from app.api import auth, health, pages
from app.api.errors import error_response, register_error_handlers
from app.api.v1 import admin, backtests, data, operations, strategies
from app.api.v1.common import InMemoryResearchRepository
from app.application.data_import_service import DataImportApplicationService
from app.core.config import Settings, load_settings
from app.core.contracts import (
    AuditEvent,
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
    Role,
    SessionRegistry,
    TokenService,
)
from app.infrastructure.db.session import database_is_ready, make_engine
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from app.infrastructure.repositories.runtime import (
    SqlAlchemyAccountDirectory,
    SqlAlchemyAuditWriter,
    SqlAlchemyIdempotencyStore,
    SqlAlchemyJobRunStore,
    SqlAlchemySessionRegistry,
)
from app.jobs.idempotency import InMemoryJobRunStore, JobRunStore
from app.jobs.queue import QueueSettings, redis_connection

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
    repository: InMemoryResearchRepository | SqlAlchemyResearchRepository | None = None,
) -> FastAPI:
    """Build an application with replaceable process-local adapters for testing."""
    runtime_settings = settings or load_settings()
    app = FastAPI(title="A-share Quantitative Validation", version="0.1.0")
    static_dir = Path(__file__).resolve().parents[1] / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.state.settings = runtime_settings
    app.state.readiness_checks = readiness_checks or {
        "database": lambda: True,
        "queue": lambda: True,
    }
    session_registry: SessionRegistry = InMemorySessionRegistry()
    job_run_store: JobRunStore = InMemoryJobRunStore()
    account_directory: Any = None
    if repository is not None:
        app.state.repository = repository
        app.state.audit_writer = audit_writer or InMemoryAuditWriter()
        app.state.idempotency_store = idempotency_store or InMemoryIdempotencyStore()
    elif runtime_settings.app_env == "test":
        app.state.repository = InMemoryResearchRepository()
        app.state.audit_writer = audit_writer or InMemoryAuditWriter()
        app.state.idempotency_store = idempotency_store or InMemoryIdempotencyStore()
    else:
        engine = make_engine(runtime_settings.database_url)
        if readiness_checks is None:
            app.state.readiness_checks = {
                "database": lambda: database_is_ready(engine),
                "queue": lambda: _redis_is_ready(),
            }
        app.state.repository = SqlAlchemyResearchRepository.from_engine(
            engine,
            create_schema=runtime_settings.app_env == "development",
        )
        app.state.audit_writer = audit_writer or SqlAlchemyAuditWriter.from_engine(engine)
        app.state.idempotency_store = idempotency_store or SqlAlchemyIdempotencyStore.from_engine(
            engine
        )
        job_run_store = SqlAlchemyJobRunStore.from_engine(engine)
        session_registry = SqlAlchemySessionRegistry.from_engine(engine)
        if accounts is None:
            account_directory = SqlAlchemyAccountDirectory.from_engine(engine)
    password_hasher = PasswordHasher()
    runtime_accounts = dict(accounts or {})
    if (
        account_directory is not None
        and runtime_settings.app_env == "development"
        and runtime_settings.development_username
        and runtime_settings.development_password
    ):
        account_created = account_directory.bootstrap(
            runtime_settings.development_username,
            password_hasher.hash(runtime_settings.development_password),
            frozenset({Role.USER, Role.REVIEWER, Role.ADMIN}),
        )
        if account_created:
            app.state.audit_writer.append(
                AuditEvent(
                    occurred_at=datetime.now(UTC),
                    actor_id=None,
                    actor_roles=("SYSTEM",),
                    action="LOCAL_DEVELOPMENT_ACCOUNT_BOOTSTRAP",
                    object_type="user_account",
                    object_id=runtime_settings.development_username,
                    request_id="startup",
                    result="SUCCESS",
                    after_summary={"roles": ["USER", "REVIEWER", "ADMIN"]},
                )
            )
    app.state.authenticator = LocalAuthenticator(
        accounts=runtime_accounts,
        password_hasher=password_hasher,
        token_service=TokenService(runtime_settings.auth_secret_key),
        sessions=session_registry,
        account_directory=account_directory,
    )
    app.state.data_import_service = DataImportApplicationService(app.state.repository)
    if (
        isinstance(app.state.repository, SqlAlchemyResearchRepository)
        and runtime_settings.app_env == "development"
    ):
        seed_result = app.state.repository.initialize_local_default_versions()
        if seed_result["created"]:
            app.state.audit_writer.append(
                AuditEvent(
                    occurred_at=datetime.now(UTC),
                    actor_id=None,
                    actor_roles=("SYSTEM",),
                    action="LOCAL_DEFAULT_CONFIG_INITIALIZE",
                    object_type="configuration_versions",
                    object_id="cost_v1,rule_v1",
                    request_id="startup",
                    result="SUCCESS",
                    after_summary=seed_result,
                )
            )
    app.state.job_run_store = job_run_store
    configure_logging()
    register_error_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied_request_id = request.headers.get("X-Request-Id", "")
        request_id = (
            supplied_request_id
            if _REQUEST_ID.fullmatch(supplied_request_id)
            else f"req_{uuid4().hex}"
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
                            [
                                request.method.encode(),
                                request.url.path.encode(),
                                body,
                                actor_id.encode(),
                            ]
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
                response_body = _with_current_request_id(response_body, content_type, request_id)
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
    app.include_router(pages.router)
    return app


def _redis_is_ready() -> bool:
    try:
        redis_connection(QueueSettings.from_env())
    except Exception:
        return False
    return True
