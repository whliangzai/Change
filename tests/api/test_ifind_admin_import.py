from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.errors import DependencyError
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def _client(*, enabled: bool = True):
    hasher = PasswordHasher()
    admin_id = uuid4()
    accounts = {
        "admin": LocalAccount(admin_id, "admin", hasher.hash("pw"), frozenset({Role.ADMIN})),
        "user": LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER})),
    }
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "IFIND_ENABLED": "true" if enabled else "false",
            "IFIND_REFRESH_TOKEN": "deployment-secret",
        }
    )
    app = create_app(settings=settings, accounts=accounts)
    calls: list[dict[str, object]] = []

    def enqueue(function, *args, **kwargs):
        calls.append({"function": function, "args": args, "kwargs": kwargs})
        return type("Job", (), {"id": kwargs["job_id"]})()

    app.state.ifind_enqueue = enqueue
    client = TestClient(app)
    token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "login-admin"},
        json={"username": "admin", "password": "pw"},
    ).json()["data"]["access_token"]
    user_token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "login-user"},
        json={"username": "user", "password": "pw"},
    ).json()["data"]["access_token"]
    return client, token, user_token, calls, admin_id


def test_ifind_admin_import_is_rbac_idempotent_and_returns_stable_task_key() -> None:
    client, token, _, calls, admin_id = _client()
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "ifind-20250102-pilot"}
    first = client.post(
        "/api/v1/admin/data-imports/ifind/2025-01-02?scope=pilot",
        headers=headers,
    )
    replay = client.post(
        "/api/v1/admin/data-imports/ifind/2025-01-02?scope=pilot",
        headers=headers,
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert first.json()["data"]["task_key"] == "data-import:2025-01-02:ifind-pilot"
    assert replay.json()["data"] == first.json()["data"]
    assert len(calls) == 1
    assert calls[0]["args"] == ("2025-01-02", "pilot", str(admin_id))
    assert first.json()["data"]["value"]["owner_id"] == str(admin_id)


def test_ifind_admin_import_requires_admin_and_enabled_provider() -> None:
    client, _, user_token, _, _ = _client()
    denied = client.post(
        "/api/v1/admin/data-imports/ifind/2025-01-02?scope=pilot",
        headers={"Authorization": f"Bearer {user_token}", "Idempotency-Key": "ifind-user"},
    )
    assert denied.status_code == 403

    disabled_client, disabled_token, _, _, _ = _client(enabled=False)
    disabled = disabled_client.post(
        "/api/v1/admin/data-imports/ifind/2025-01-02?scope=pilot",
        headers={
            "Authorization": f"Bearer {disabled_token}",
            "Idempotency-Key": "ifind-disabled",
        },
    )
    assert disabled.status_code == 503


def test_ifind_admin_import_requires_idempotency_key() -> None:
    client, token, _, _, _ = _client()
    response = client.post(
        "/api/v1/admin/data-imports/ifind/2025-01-02?scope=pilot",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


def test_ifind_admin_import_surfaces_queue_unavailable() -> None:
    client, token, _, _, _ = _client()

    def unavailable(*_args, **_kwargs):
        raise DependencyError("Redis queue is unavailable")

    client.app.state.ifind_enqueue = unavailable
    response = client.post(
        "/api/v1/admin/data-imports/ifind/2025-01-02?scope=full",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "ifind-queue-down"},
    )
    assert response.status_code == 503
