"""FastAPI dependencies for authenticated, auditable operations."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, Request

from app.core.contracts import AuditWriter
from app.core.errors import ValidationError
from app.core.security import AuthenticationError, LocalAuthenticator, Principal, Role


def get_request_id(request: Request) -> str:
    return str(request.state.request_id)


def get_audit_writer(request: Request) -> AuditWriter:
    return request.app.state.audit_writer  # type: ignore[no-any-return]


def get_authenticator(request: Request) -> LocalAuthenticator:
    return request.app.state.authenticator  # type: ignore[no-any-return]


def get_current_principal(
    authenticator: Annotated[LocalAuthenticator, Depends(get_authenticator)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AuthenticationError("bearer authentication is required")
    return authenticator.authenticate_access_token(authorization.removeprefix("Bearer "))


def require_roles(*roles: Role) -> Callable[[Principal], Principal]:
    def dependency(
        principal: Annotated[Principal, Depends(get_current_principal)],
        authenticator: Annotated[LocalAuthenticator, Depends(get_authenticator)],
    ) -> Principal:
        authenticator.require_roles(principal, *roles)
        return principal

    return dependency


def require_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise ValidationError("Idempotency-Key is required for mutating requests")
    return value
