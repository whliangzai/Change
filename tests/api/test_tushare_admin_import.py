from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def test_tushare_import_is_admin_only_and_uses_a_stable_scope_key() -> None:
    hasher = PasswordHasher()
    accounts = {
        "admin": LocalAccount(uuid4(), "admin", hasher.hash("pw"), frozenset({Role.ADMIN})),
        "user": LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER})),
    }
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "TUSHARE_ENABLED": "true",
            "TUSHARE_TOKEN": "deployment-token",
        }
    )
    app = create_app(settings=settings, accounts=accounts)
    calls = []
    app.state.tushare_enqueue = lambda function, *args, **kwargs: (
        calls.append((function, args, kwargs)) or type("Job", (), {"id": kwargs["job_id"]})()
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "tushare-login"},
        json={"username": "admin", "password": "pw"},
    )
    token = login.json()["data"]["access_token"]
    response = client.post(
        "/api/v1/admin/data-imports/tushare/2025-01-02?scope=pilot",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "tushare-pilot"},
    )
    replay = client.post(
        "/api/v1/admin/data-imports/tushare/2025-01-02?scope=pilot",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "tushare-pilot"},
    )
    assert response.status_code == replay.status_code == 202
    assert response.json()["data"]["task_key"] == "data-import:2025-01-02:tushare-pilot"
    assert len(calls) == 1
