from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.security import LocalAccount, PasswordHasher, Role
from app.main import create_app


@pytest.mark.parametrize("environment", ["test", "development"])
def test_strategy_diff_provides_current_evidence_without_expanding_access(
    environment, tmp_path
) -> None:
    owner = uuid4()
    hasher = PasswordHasher()
    accounts = {
        "owner": LocalAccount(owner, "owner", hasher.hash("pw"), frozenset({Role.USER})),
        "other": LocalAccount(
            uuid4(), "other", hasher.hash("pw"), frozenset({Role.USER, Role.REVIEWER})
        ),
    }
    settings = load_settings(
        {
            "APP_ENV": environment,
            "AUTH_SECRET_KEY": "development-only-test-evidence-secret",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'evidence.db'}",
        }
    )
    app = create_app(settings=settings, accounts=accounts)
    with TestClient(app) as client:
        record = app.state.repository.create_strategy(
            owner,
            {"name": "evidence", "parameters": {"lookback": 20}, "change_reason": "initial"},
        )
        identifier = record["strategy_version_id"]

        def login(username):
            response = client.post(
                "/api/v1/auth/login",
                headers={"Idempotency-Key": f"login-{username}"},
                json={"username": username, "password": "pw"},
            )
            return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}

        headers = login("owner")
        detail = client.get(f"/api/v1/strategies/{identifier}/diff", headers=headers)
        assert detail.status_code == 200
        data = detail.json()["data"]
        assert data["strategy_version_id"] == identifier
        assert data["status"] == "DRAFT"
        assert data["name"] == "evidence"
        assert data["change_reason"] == "initial"
        assert data["changes"] == {"lookback": 20}
        assert data["base_version"] is None
        assert (
            client.get(f"/api/v1/strategies/{identifier}/diff", headers=login("other")).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/strategies/{identifier}/submit-review",
                headers=headers | {"Idempotency-Key": "forbidden-review"},
                json={"decision": "PUBLISH", "review_note": "not authorized"},
            ).status_code
            == 403
        )
        app.state.repository.submit_strategy(
            identifier, owner, "PUBLISH", "reviewed", allow_reviewer=True
        )
        refreshed = client.get(f"/api/v1/strategies/{identifier}/diff", headers=headers)
        assert refreshed.json()["data"]["status"] == "PUBLISHED"
