from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.contracts import InMemoryAuditWriter
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app

TEST_SETTINGS = load_settings(
    {"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"}
)


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
    return (
        TestClient(
            create_app(settings=TEST_SETTINGS, accounts=accounts, audit_writer=audit_writer)
        ),
        audit_writer,
    )


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
    assert audit_writer.events[0].idempotency_key == "login-user"


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


def test_mutating_requests_replay_identical_results_and_reject_key_reuse_with_new_input() -> None:
    client, audit_writer = make_client()
    headers = {"Idempotency-Key": "stable-login"}

    first = client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"username": "user", "password": "correct horse battery staple"},
    )
    replay = client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"username": "user", "password": "correct horse battery staple"},
    )
    conflict = client.post(
        "/api/v1/auth/login",
        headers=headers,
        json={"username": "reviewer", "password": "correct horse battery staple"},
    )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["data"] == first.json()["data"]
    assert first.json()["request_id"] == first.headers["X-Request-Id"]
    assert replay.json()["request_id"] == replay.headers["X-Request-Id"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(audit_writer.events) == 1


def test_logout_with_missing_or_invalid_refresh_token_clears_page_cookie_but_returns_401() -> None:
    client, _ = make_client()

    login(client, "admin", "page-cookie-login")
    missing = client.post(
        "/api/v1/auth/logout",
        headers={"Idempotency-Key": "page-cookie-logout-missing"},
        json={},
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTH_REQUIRED"
    assert 'research_page_access=""' in missing.headers["set-cookie"]
    assert client.get("/data-import").status_code == 401

    login(client, "admin", "page-cookie-login-empty")
    empty = client.post(
        "/api/v1/auth/logout",
        headers={"Idempotency-Key": "page-cookie-logout-empty"},
        json={"refresh_token": ""},
    )

    assert empty.status_code == 401
    assert empty.json()["error"]["code"] == "AUTH_REQUIRED"
    assert 'research_page_access=""' in empty.headers["set-cookie"]
    assert client.get("/daily-flow").status_code == 401

    login(client, "admin", "page-cookie-login-invalid")
    invalid = client.post(
        "/api/v1/auth/logout",
        headers={"Idempotency-Key": "page-cookie-logout-invalid"},
        json={"refresh_token": "not-a-refresh-token"},
    )

    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "AUTH_REQUIRED"
    assert 'research_page_access=""' in invalid.headers["set-cookie"]
    assert client.get("/daily-flow").status_code == 401

    tokens = login(client, "admin", "page-cookie-login-revoked")
    first_logout = client.post(
        "/api/v1/auth/logout",
        headers={"Idempotency-Key": "page-cookie-logout-first"},
        json={"refresh_token": tokens["refresh_token"]},
    )
    revoked = client.post(
        "/api/v1/auth/logout",
        headers={"Idempotency-Key": "page-cookie-logout-revoked"},
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert first_logout.status_code == 200
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "AUTH_REQUIRED"
    assert 'research_page_access=""' in revoked.headers["set-cookie"]


def test_development_account_is_bootstrapped_into_its_database(tmp_path) -> None:
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'bootstrap-auth.db'}",
            "DEVELOPMENT_USERNAME": "local-user",
            "DEVELOPMENT_PASSWORD": "local-password",
        }
    )
    client = TestClient(create_app(settings=settings))

    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "development-account-login"},
        json={"username": "local-user", "password": "local-password"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["token_type"] == "bearer"
    restarted = TestClient(create_app(settings=settings))
    restarted_response = restarted.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "development-account-login-restarted"},
        json={"username": "local-user", "password": "local-password"},
    )
    assert restarted_response.status_code == 200


def test_fresh_development_database_bootstraps_documented_admin_account(tmp_path) -> None:
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'documented-admin.db'}",
        }
    )
    client = TestClient(create_app(settings=settings))

    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "documented-admin-login"},
        json={"username": "admin", "password": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["token_type"] == "bearer"
