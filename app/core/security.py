"""Local Argon2id authentication, token issuance, revocation, and RBAC."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

import jwt
from argon2 import PasswordHasher as Argon2PasswordHasher
from argon2 import Type
from argon2.exceptions import VerificationError


class AuthenticationError(Exception):
    """Raised when credentials or session state cannot be trusted."""


class AccessDeniedError(Exception):
    """Raised when a verified principal lacks a required role."""


class Role(StrEnum):
    USER = "USER"
    REVIEWER = "REVIEWER"
    ADMIN = "ADMIN"


@dataclass(frozen=True, slots=True)
class LocalAccount:
    id: UUID
    username: str
    password_hash: str
    roles: frozenset[Role]
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: UUID
    username: str
    roles: frozenset[Role]
    session_id: UUID


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str


class PasswordHasher:
    def __init__(self) -> None:
        self._hasher = Argon2PasswordHasher(type=Type.ID)

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except VerificationError:
            return False


class TokenService:
    ACCESS_TOKEN_LIFETIME = timedelta(minutes=30)
    REFRESH_TOKEN_LIFETIME = timedelta(days=7)

    def __init__(self, secret_key: str) -> None:
        self._secret_key = secret_key

    def issue(self, account: LocalAccount, session_id: UUID) -> TokenPair:
        return TokenPair(
            access_token=self._encode(account, session_id, "access", self.ACCESS_TOKEN_LIFETIME),
            refresh_token=self._encode(account, session_id, "refresh", self.REFRESH_TOKEN_LIFETIME),
        )

    def decode(self, token: str, expected_kind: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(token, self._secret_key, algorithms=["HS256"])
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid or expired token") from exc
        if claims.get("kind") != expected_kind:
            raise AuthenticationError("unexpected token type")
        return claims

    def _encode(
        self, account: LocalAccount, session_id: UUID, kind: str, lifetime: timedelta
    ) -> str:
        now = datetime.now(UTC)
        claims = {
            "sub": str(account.id),
            "username": account.username,
            "roles": [role.value for role in account.roles],
            "sid": str(session_id),
            "kind": kind,
            "iat": now,
            "exp": now + lifetime,
        }
        return str(jwt.encode(claims, self._secret_key, algorithm="HS256"))


class InMemorySessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[UUID, UUID] = {}
        self._revoked_sessions: set[UUID] = set()
        self._lock = Lock()

    def create(self, user_id: UUID) -> UUID:
        session_id = uuid4()
        self._sessions[session_id] = user_id
        return session_id

    def revoke(self, session_id: UUID) -> None:
        self._revoked_sessions.add(session_id)

    def revoke_user(self, user_id: UUID) -> None:
        self._revoked_sessions.update(
            session_id for session_id, owner_id in self._sessions.items() if owner_id == user_id
        )

    def is_active(self, session_id: UUID, user_id: UUID) -> bool:
        return (
            self._sessions.get(session_id) == user_id and session_id not in self._revoked_sessions
        )

    def consume(self, session_id: UUID, user_id: UUID) -> bool:
        with self._lock:
            if not self.is_active(session_id, user_id):
                return False
            self._revoked_sessions.add(session_id)
            return True


class LocalAuthenticator:
    def __init__(
        self,
        accounts: dict[str, LocalAccount],
        password_hasher: PasswordHasher,
        token_service: TokenService,
        sessions: InMemorySessionRegistry,
    ) -> None:
        self._accounts = accounts
        self._password_hasher = password_hasher
        self._token_service = token_service
        self._sessions = sessions

    def login(self, username: str, password: str) -> TokenPair:
        account = self._accounts.get(username)
        if (
            account is None
            or not account.enabled
            or not self._password_hasher.verify(account.password_hash, password)
        ):
            raise AuthenticationError("invalid credentials")
        session_id = self._sessions.create(account.id)
        return self._token_service.issue(account, session_id)

    def authenticate_access_token(self, token: str) -> Principal:
        claims = self._token_service.decode(token, "access")
        return self._principal_from_claims(claims)

    def refresh(self, refresh_token: str) -> TokenPair:
        claims = self._token_service.decode(refresh_token, "refresh")
        principal = self._principal_from_claims(claims)
        if not self._sessions.consume(principal.session_id, principal.user_id):
            raise AuthenticationError("refresh token was already consumed")
        account = self._accounts[principal.username]
        return self._token_service.issue(account, self._sessions.create(account.id))

    def logout(self, refresh_token: str) -> None:
        claims = self._token_service.decode(refresh_token, "refresh")
        self._sessions.revoke(UUID(str(claims["sid"])))

    def disable_account(self, user_id: UUID) -> None:
        self._set_account_enabled(user_id, False)
        self._sessions.revoke_user(user_id)

    def enable_account(self, user_id: UUID) -> None:
        self._set_account_enabled(user_id, True)

    def revoke_user_sessions(self, user_id: UUID) -> None:
        self._sessions.revoke_user(user_id)

    def set_roles(self, user_id: UUID, roles: frozenset[Role]) -> None:
        for username, account in self._accounts.items():
            if account.id == user_id:
                self._accounts[username] = replace(account, roles=roles)
                return
        raise AuthenticationError("account not found")

    def require_roles(self, principal: Principal, *roles: Role) -> None:
        if not any(role in principal.roles for role in roles):
            raise AccessDeniedError("insufficient role")

    def _principal_from_claims(self, claims: dict[str, Any]) -> Principal:
        try:
            user_id = UUID(str(claims["sub"]))
            session_id = UUID(str(claims["sid"]))
            username = str(claims["username"])
            frozenset(Role(role) for role in claims["roles"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AuthenticationError("invalid token claims") from exc
        account = self._accounts.get(username)
        if account is None or account.id != user_id or not account.enabled:
            raise AuthenticationError("account is unavailable")
        if not self._sessions.is_active(session_id, user_id):
            raise AuthenticationError("session is revoked")
        return Principal(user_id, username, account.roles, session_id)

    def _set_account_enabled(self, user_id: UUID, enabled: bool) -> None:
        for username, account in self._accounts.items():
            if account.id == user_id:
                self._accounts[username] = replace(account, enabled=enabled)
                return
        raise AuthenticationError("account not found")
