from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.contracts import InMemoryAuditWriter
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def make_client() -> tuple[TestClient, InMemoryAuditWriter]:
    hasher = PasswordHasher()
    accounts = {
        name: LocalAccount(
            id=uuid4(),
            username=name,
            password_hash=hasher.hash("correct horse battery staple"),
            roles=roles,
        )
        for name, roles in {
            "user": frozenset({Role.USER}),
            "reviewer": frozenset({Role.REVIEWER}),
            "admin": frozenset({Role.ADMIN}),
        }.items()
    }
    audit_writer = InMemoryAuditWriter()
    return TestClient(create_app(accounts=accounts, audit_writer=audit_writer)), audit_writer


def login(client: TestClient, username: str, key: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": key},
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_app_factory_authentication_refresh_logout_and_audit_append() -> None:
    client, audit_writer = make_client()

    tokens = login(client, "user", "login-user")
    refreshed = client.post(
        "/api/v1/auth/refresh",
        headers={"Idempotency-Key": "refresh-user"},
        json={"refresh_token": tokens["refresh_token"]},
    )
    logout = client.post(
        "/api/v1/auth/logout",
        headers={"Idempotency-Key": "logout-user"},
        json={"refresh_token": refreshed.json()["data"]["refresh_token"]},
    )
    denied = client.get(
        "/api/v1/audit-events",
        headers={"Authorization": f"Bearer {refreshed.json()['data']['access_token']}"},
    )

    assert refreshed.status_code == 200
    assert logout.status_code == 200
    assert denied.status_code == 401
    assert [event.action for event in audit_writer.events] == ["AUTH_LOGIN"]


def test_disabled_and_revoked_accounts_are_rejected_immediately_through_the_app() -> None:
    client, _ = make_client()
    app = client.app
    disabled = login(client, "user", "disabled-user")
    user_id = app.state.authenticator.authenticate_access_token(disabled["access_token"]).user_id
    app.state.authenticator.disable_account(user_id)
    disabled_response = client.get(
        "/api/v1/audit-events",
        headers={"Authorization": f"Bearer {disabled['access_token']}"},
    )
    app.state.authenticator.enable_account(user_id)
    revoked = login(client, "user", "revoked-user")
    app.state.authenticator.revoke_user_sessions(user_id)
    revoked_response = client.get(
        "/api/v1/audit-events",
        headers={"Authorization": f"Bearer {revoked['access_token']}"},
    )

    assert disabled_response.status_code == 401
    assert revoked_response.status_code == 401


def test_role_guards_allow_reviewer_and_admin_but_reject_user() -> None:
    client, _ = make_client()
    user = login(client, "user", "role-user")
    reviewer = login(client, "reviewer", "role-reviewer")
    admin = login(client, "admin", "role-admin")

    responses = {
        role: client.get(
            "/api/v1/audit-events",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        for role, tokens in {"user": user, "reviewer": reviewer, "admin": admin}.items()
    }

    assert responses["user"].status_code == 403
    assert responses["user"].json()["error"]["code"] == "FORBIDDEN"
    assert responses["reviewer"].status_code == 200
    assert responses["admin"].status_code == 200
