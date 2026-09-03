from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def test_daily_flow_exposes_non_trading_manual_workflow() -> None:
    hasher = PasswordHasher()
    account = LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER}))
    client = TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts={"user": account},
        )
    )
    token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "e2e-login"},
        json={"username": "user", "password": "pw"},
    ).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    report = client.get("/api/v1/daily-reports/2026-09-03", headers=headers)

    assert report.status_code == 200
    assert "不构成投资建议" in report.json()["data"]["notice"]
    assert "不自动下单" in report.json()["data"]["notice"]
    assert client.get("/api/v1/broker/order-submit", headers=headers).status_code == 404
