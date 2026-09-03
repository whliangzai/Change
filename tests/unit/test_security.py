from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Lock
from uuid import UUID, uuid4

import pytest

from app.core.security import (
    AccessDeniedError,
    AuthenticationError,
    InMemorySessionRegistry,
    LocalAccount,
    LocalAuthenticator,
    PasswordHasher,
    Role,
    TokenService,
)


def make_authenticator() -> tuple[LocalAuthenticator, LocalAccount]:
    hasher = PasswordHasher()
    account = LocalAccount(
        id=uuid4(),
        username="researcher",
        password_hash=hasher.hash("correct horse battery staple"),
        roles=frozenset({Role.USER, Role.REVIEWER}),
    )
    return (
        LocalAuthenticator(
            accounts={account.username: account},
            password_hasher=hasher,
            token_service=TokenService("test-secret-that-is-long-enough!"),
            sessions=InMemorySessionRegistry(),
        ),
        account,
    )


def test_local_passwords_use_argon2id_and_issue_documented_token_lifetimes() -> None:
    authenticator, account = make_authenticator()

    tokens = authenticator.login(account.username, "correct horse battery staple")
    principal = authenticator.authenticate_access_token(tokens.access_token)

    assert account.password_hash.startswith("$argon2id$")
    assert principal.user_id == account.id
    assert principal.roles == frozenset({Role.USER, Role.REVIEWER})
    assert timedelta(minutes=30) == TokenService.ACCESS_TOKEN_LIFETIME
    assert timedelta(days=7) == TokenService.REFRESH_TOKEN_LIFETIME


def test_logout_disabling_and_administrator_revocation_invalidate_tokens_immediately() -> None:
    authenticator, account = make_authenticator()

    logged_out = authenticator.login(account.username, "correct horse battery staple")
    authenticator.logout(logged_out.refresh_token)
    with pytest.raises(AuthenticationError):
        authenticator.authenticate_access_token(logged_out.access_token)

    disabled = authenticator.login(account.username, "correct horse battery staple")
    authenticator.disable_account(account.id)
    with pytest.raises(AuthenticationError):
        authenticator.authenticate_access_token(disabled.access_token)

    authenticator.enable_account(account.id)
    revoked = authenticator.login(account.username, "correct horse battery staple")
    authenticator.revoke_user_sessions(account.id)
    with pytest.raises(AuthenticationError):
        authenticator.authenticate_access_token(revoked.access_token)


def test_role_requirement_enforces_user_reviewer_and_admin_boundaries() -> None:
    authenticator, account = make_authenticator()
    principal = authenticator.authenticate_access_token(
        authenticator.login(account.username, "correct horse battery staple").access_token
    )

    authenticator.require_roles(principal, Role.USER)
    authenticator.require_roles(principal, Role.REVIEWER)
    with pytest.raises(AccessDeniedError):
        authenticator.require_roles(principal, Role.ADMIN)


def test_refresh_token_is_consumed_atomically_when_two_callers_race() -> None:
    _, account = make_authenticator()
    barrier = Barrier(2)

    class BarrierSessionRegistry(InMemorySessionRegistry):
        def __init__(self) -> None:
            super().__init__()
            self._checks = 0
            self._checks_lock = Lock()

        def is_active(self, session_id: UUID, user_id: UUID) -> bool:
            active = super().is_active(session_id, user_id)
            with self._checks_lock:
                self._checks += 1
                should_wait = self._checks <= 2
            if active and should_wait:
                barrier.wait()
            return active

    hasher = PasswordHasher()
    authenticator = LocalAuthenticator(
        accounts={account.username: account},
        password_hasher=hasher,
        token_service=TokenService("test-secret-that-is-long-enough!"),
        sessions=BarrierSessionRegistry(),
    )
    refresh_token = authenticator.login(account.username, "correct horse battery staple").refresh_token

    def refresh_once() -> bool:
        barrier.wait()
        try:
            authenticator.refresh(refresh_token)
        except AuthenticationError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: refresh_once(), range(2)))

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_current_account_roles_override_roles_embedded_in_an_old_access_token() -> None:
    authenticator, account = make_authenticator()
    authenticator.set_roles(account.id, frozenset({Role.ADMIN}))
    token = authenticator.login(account.username, "correct horse battery staple").access_token
    authenticator.set_roles(account.id, frozenset({Role.USER}))

    principal = authenticator.authenticate_access_token(token)

    assert principal.roles == frozenset({Role.USER})
    with pytest.raises(AccessDeniedError):
        authenticator.require_roles(principal, Role.ADMIN)
