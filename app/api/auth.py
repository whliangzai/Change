"""Local-account authentication endpoints; no broker credentials are accepted."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.health import _success
from app.core.contracts import AuditEvent, AuditWriter
from app.core.dependencies import get_audit_writer, get_authenticator, get_request_id
from app.core.security import LocalAuthenticator

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
AuthenticatorDependency = Annotated[LocalAuthenticator, Depends(get_authenticator)]
AuditWriterDependency = Annotated[AuditWriter, Depends(get_audit_writer)]
RequestIdDependency = Annotated[str, Depends(get_request_id)]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


def _token_data(access_token: str, refresh_token: str) -> dict[str, str]:
    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    authenticator: AuthenticatorDependency,
    audit_writer: AuditWriterDependency,
    request_id: RequestIdDependency,
) -> JSONResponse:
    tokens = authenticator.login(payload.username, payload.password)
    principal = authenticator.authenticate_access_token(tokens.access_token)
    audit_writer.append(
        AuditEvent(
            occurred_at=datetime.now(UTC),
            actor_id=principal.user_id,
            actor_roles=tuple(sorted(role.value for role in principal.roles)),
            action="AUTH_LOGIN",
            object_type="user",
            object_id=str(principal.user_id),
            request_id=request_id,
            result="SUCCESS",
            idempotency_key=getattr(request.state, "idempotency_key", None),
        )
    )
    return _success(request, _token_data(tokens.access_token, tokens.refresh_token))


@router.post("/refresh")
def refresh(
    payload: RefreshRequest,
    request: Request,
    authenticator: AuthenticatorDependency,
) -> JSONResponse:
    tokens = authenticator.refresh(payload.refresh_token)
    return _success(request, _token_data(tokens.access_token, tokens.refresh_token))


@router.post("/logout")
def logout(
    payload: LogoutRequest,
    request: Request,
    authenticator: AuthenticatorDependency,
) -> JSONResponse:
    authenticator.logout(payload.refresh_token)
    return _success(request, {"status": "logged_out"})
