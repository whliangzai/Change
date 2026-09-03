from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def make_client() -> TestClient:
    hasher = PasswordHasher()
    accounts = {
        role: LocalAccount(uuid4(), role, hasher.hash("pw"), frozenset({role_value}))
        for role, role_value in {"user": Role.USER, "reviewer": Role.REVIEWER}.items()
    }
    return TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts=accounts,
        )
    )


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": f"login-{username}"},
        json={"username": username, "password": "pw"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_user_cannot_confirm_and_execution_keeps_plan_separate() -> None:
    client = make_client()
    denied = client.post(
        "/api/v1/order-plans/plan_missing/confirm",
        headers=login(client, "user") | {"Idempotency-Key": "confirm-denied"},
        json={"decision": "CONFIRM", "review_note": "checked", "expected_version": 1},
    )
    assert denied.status_code == 403

    execution = client.post(
        "/api/v1/executions",
        headers=login(client, "user") | {"Idempotency-Key": "execution-missing"},
        json={
            "plan_id": "plan_missing",
            "execution_type": "MANUAL_ENTRY",
            "executed_at": "2026-09-04T09:42:10+08:00",
            "quantity": 100,
            "price": "12.35",
            "commission": "5.00",
            "stamp_tax": "0.00",
            "transfer_fee": "0.02",
            "other_fee": "0.00",
            "unfilled_quantity": 0,
            "note": "manual",
        },
    )
    assert execution.status_code == 404


def test_execution_replay_is_idempotent_and_partial_fill_is_represented() -> None:
    client = make_client()
    reviewer = login(client, "reviewer")
    plan = client.get("/api/v1/order-plans?execution_date=2026-09-04", headers=reviewer)
    assert plan.status_code == 200
    assert plan.json()["data"]["items"]
    plan_id = plan.json()["data"]["items"][0]["plan_id"]

    confirmed = client.post(
        f"/api/v1/order-plans/{plan_id}/confirm",
        headers=reviewer | {"Idempotency-Key": "confirm-1"},
        json={"decision": "CONFIRM", "review_note": "checked", "expected_version": 1},
    )
    assert confirmed.status_code == 200
    payload = {
        "plan_id": plan_id,
        "execution_type": "MANUAL_ENTRY",
        "executed_at": "2026-09-04T09:42:10+08:00",
        "quantity": 100,
        "price": "12.35",
        "commission": "5.00",
        "stamp_tax": "0.00",
        "transfer_fee": "0.02",
        "other_fee": "0.00",
        "unfilled_quantity": 200,
        "note": "partial",
    }
    first = client.post(
        "/api/v1/executions",
        headers=login(client, "user") | {"Idempotency-Key": "execution-1"},
        json=payload,
    )
    replay = client.post(
        "/api/v1/executions",
        headers=login(client, "user") | {"Idempotency-Key": "execution-1"},
        json=payload,
    )
    assert first.status_code == 201
    assert first.json()["data"]["status"] == "PARTIALLY_FILLED"
    assert replay.json()["data"] == first.json()["data"]
