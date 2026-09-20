from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


def _client() -> TestClient:
    account = LocalAccount(
        uuid4(),
        "user",
        PasswordHasher().hash("pw"),
        frozenset({Role.USER}),
    )
    return TestClient(
        create_app(
            settings=load_settings(
                {"APP_ENV": "test", "AUTH_SECRET_KEY": "strategy-catalog-test-secret"}
            ),
            accounts={"user": account},
        )
    )


def _auth(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "strategy-catalog-login"},
        json={"username": "user", "password": "pw"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_catalog_and_typed_strategy_creation_contract() -> None:
    client = _client()
    headers = _auth(client)
    catalog = client.get("/api/v1/strategies/catalog", headers=headers)
    assert catalog.status_code == 200
    assert {item["strategy_type"] for item in catalog.json()["data"]["items"]} == {
        "STRONG_TREND",
        "MA_TREND",
    }
    ma_catalog = next(
        item for item in catalog.json()["data"]["items"] if item["strategy_type"] == "MA_TREND"
    )
    assert ma_catalog["display_name"] == "MA20/MA60 均线趋势"
    assert ma_catalog["parameter_schema"]["properties"]["short_window"]["title"] == "短期均线窗口"
    assert "交易日" in ma_catalog["parameter_schema"]["properties"]["short_window"]["description"]

    created = client.post(
        "/api/v1/strategies",
        headers=headers | {"Idempotency-Key": "ma-create"},
        json={
            "name": "ma",
            "change_reason": "initial",
            "strategy_type": "MA_TREND",
            "parameters": {},
        },
    )
    assert created.status_code == 201
    assert created.json()["data"]["strategy_type"] == "MA_TREND"
    assert created.json()["data"]["parameters"]["long_window"] == 60

    invalid = client.post(
        "/api/v1/strategies",
        headers=headers | {"Idempotency-Key": "ma-invalid"},
        json={
            "name": "invalid",
            "change_reason": "invalid parameter",
            "strategy_type": "MA_TREND",
            "parameters": {"mystery": 1},
        },
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
