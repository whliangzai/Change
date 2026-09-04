from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.v1.common import InMemoryResearchRepository
from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


class DailyFlowRepository(InMemoryResearchRepository):
    def run_daily_flow(self, owner_id, payload):  # type: ignore[no-untyped-def]
        return {
            "run_id": "daily_123",
            "owner_id": str(owner_id),
            "status": "SUCCEEDED",
            "result_usable": True,
            "as_of_date": payload["as_of_date"],
            "plan_count": 0,
        }


def test_daily_flow_route_delegates_to_application_repository() -> None:
    owner_id = uuid4()
    account = LocalAccount(
        owner_id,
        "user",
        PasswordHasher().hash("pw"),
        frozenset({Role.USER, Role.REVIEWER, Role.ADMIN}),
    )
    app = create_app(
        settings=load_settings(
            {
                "APP_ENV": "test",
                "AUTH_SECRET_KEY": "test-secret-key",
            }
        ),
        accounts={"user": account},
        repository=DailyFlowRepository(),
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "daily-route-login"},
        json={"username": "user", "password": "pw"},
    )
    headers = {
        "Authorization": f"Bearer {login.json()['data']['access_token']}",
        "Idempotency-Key": "daily-route-run",
    }
    response = client.post(
        "/api/v1/daily-flows",
        headers=headers,
        json={
            "data_batch_id": str(uuid4()),
            "strategy_version_id": str(uuid4()),
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "as_of_date": date(2026, 9, 25).isoformat(),
            "information_cutoff_at": "2026-09-25T18:00:00+00:00",
            "initial_equity": "20000.00",
            "max_investment_ratio": "0.70",
        },
    )

    assert response.status_code == 202
    assert response.json()["data"]["run_id"] == "daily_123"
    assert app.state.audit_writer.events[-1].object_type == "daily_flow_run"


def test_daily_flow_requires_admin_role() -> None:
    owner_id = uuid4()
    account = LocalAccount(
        owner_id,
        "user",
        PasswordHasher().hash("pw"),
        frozenset({Role.USER, Role.REVIEWER}),
    )
    app = create_app(
        settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-key"}),
        accounts={"user": account},
        repository=DailyFlowRepository(),
    )
    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "daily-forbidden-login"},
        json={"username": "user", "password": "pw"},
    )

    response = client.post(
        "/api/v1/daily-flows",
        headers={
            "Authorization": f"Bearer {login.json()['data']['access_token']}",
            "Idempotency-Key": "daily-user-forbidden",
        },
        json={
            "data_batch_id": str(uuid4()),
            "strategy_version_id": str(uuid4()),
            "as_of_date": date(2026, 9, 25).isoformat(),
            "information_cutoff_at": "2026-09-25T18:00:00+00:00",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_daily_flow_requires_authentication() -> None:
    response = TestClient(
        create_app(
            settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-key"}),
            repository=DailyFlowRepository(),
        )
    ).post(
        "/api/v1/daily-flows",
        headers={"Idempotency-Key": "daily-auth-required"},
        json={
            "data_batch_id": str(uuid4()),
            "strategy_version_id": str(uuid4()),
            "as_of_date": date(2026, 9, 25).isoformat(),
            "information_cutoff_at": "2026-09-25T18:00:00+00:00",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"
