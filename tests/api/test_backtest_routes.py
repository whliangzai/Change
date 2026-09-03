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
        headers={"Idempotency-Key": "login-bt"},
        json={"username": "user", "password": "pw"},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def test_backtest_creation_blocks_missing_data_cost_or_rule() -> None:
    client = make_client()
    response = client.post(
        "/api/v1/backtests",
        headers=auth(client) | {"Idempotency-Key": "bt-missing"},
        json={
            "data_batch_id": "missing",
            "strategy_version_id": "missing",
            "cost_config_id": "missing",
            "rule_config_id": "missing",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "train_end": "2024-06-30",
            "valid_end": "2024-09-30",
            "oos_start": "2024-10-01",
            "benchmark_symbol": "000300.SH",
            "initial_equity": "20000.00",
            "mode": "BACKTEST",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BT_DATA_UNAVAILABLE"


def test_backtest_contract_exposes_run_trades_and_report() -> None:
    client = make_client()
    headers = auth(client) | {"Idempotency-Key": "bt-contract"}
    created = client.post(
        "/api/v1/backtests",
        headers=headers,
        json={
            "data_batch_id": "batch_available",
            "strategy_version_id": "strategy_published",
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "train_end": "2024-06-30",
            "valid_end": "2024-09-30",
            "oos_start": "2024-10-01",
            "benchmark_symbol": "000300.SH",
            "initial_equity": "20000.00",
            "mode": "BACKTEST",
        },
    )
    assert created.status_code == 202
    run_id = created.json()["data"]["run_id"]
    assert client.get(f"/api/v1/backtests/{run_id}", headers=auth(client)).status_code == 200
    assert client.get(f"/api/v1/backtests/{run_id}/trades", headers=auth(client)).status_code == 200
    assert client.get(f"/api/v1/backtests/{run_id}/report", headers=auth(client)).status_code == 200
