from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def make_client() -> TestClient:
    hasher = PasswordHasher()
    account = LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER}))
    return TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
            accounts={"user": account},
        )
    )


def auth(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "login-data"},
        json={"username": "user", "password": "pw"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_create_batch_and_quality_use_versioned_envelopes() -> None:
    client = make_client()
    headers = auth(client) | {"Idempotency-Key": "batch-1", "X-Request-Id": "req-data"}
    created = client.post(
        "/api/v1/data/batches",
        headers=headers,
        json={
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/sample.csv",
            "license_note": "licensed",
        },
    )

    assert created.status_code == 201
    batch_id = created.json()["data"]["batch_id"]
    quality = client.get(f"/api/v1/data/batches/{batch_id}/quality", headers=auth(client))
    assert quality.status_code == 200
    assert quality.json()["data"]["batch_id"] == batch_id
    assert quality.json()["data"]["quality_status"] in {"VALIDATING", "AVAILABLE", "UNAVAILABLE"}
    assert created.json()["request_id"] == "req-data"


def test_pool_requires_an_explicit_trade_date_and_paginates() -> None:
    client = make_client()
    response = client.get(
        "/api/v1/securities/pool?page=1&page_size=201",
        headers=auth(client),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
