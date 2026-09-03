from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def test_strategy_publish_is_reviewer_only_and_hidden_resources_are_not_leaked() -> None:
    hasher = PasswordHasher()
    accounts = {
        name: LocalAccount(uuid4(), name, hasher.hash("pw"), frozenset({role}))
        for name, role in {"user": Role.USER, "reviewer": Role.REVIEWER}.items()
    }
    client = TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts=accounts,
        )
    )
    token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "rbac-login"},
        json={"username": "user", "password": "pw"},
    ).json()["data"]["access_token"]
    denied = client.post(
        "/api/v1/strategies/strategy_missing/submit-review",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "strategy-publish"},
        json={"decision": "PUBLISH", "review_note": "approve"},
    )
    hidden = client.get(
        "/api/v1/backtests/not-owned",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403
    assert hidden.status_code == 404
