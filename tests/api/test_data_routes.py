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


def test_batches_can_be_listed_from_the_repository_backed_api() -> None:
    client = make_client()
    headers = auth(client) | {"Idempotency-Key": "batch-list", "X-Request-Id": "req-list"}
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

    listed = client.get("/api/v1/data/batches?page=1&page_size=50", headers=auth(client))

    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["batch_id"] == created.json()["data"]["batch_id"]


def test_authorized_csv_import_is_durable_and_queryable_after_app_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'api-import.db'}"
    source = tmp_path / "bars.csv"
    source.write_text(
        """symbol,trade_date,open,high,low,close,volume,amount,adjustment_factor,available_at
600000.SH,2026-09-03,10.00,10.20,9.90,10.10,10000,101000.00,1.0,2026-09-03T18:00:00+00:00
""",
        encoding="utf-8",
    )
    owner_id = uuid4()
    account = LocalAccount(
        owner_id, "user", PasswordHasher().hash("pw"), frozenset({Role.USER, Role.ADMIN})
    )
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": database_url,
        }
    )

    first = TestClient(create_app(settings=settings, accounts={"user": account}))
    token = first.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "api-import-login"},
        json={"username": "user", "password": "pw"},
    ).json()["data"]["access_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": "api-import-1",
        "X-Request-Id": "api-import-request",
    }
    imported = first.post(
        "/api/v1/data/imports",
        headers=headers,
        json={
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": str(source),
            "license_note": "licensed",
            "available_at": "2026-09-03T19:00:00+00:00",
            "information_cutoff_at": "2026-09-03T18:00:00+00:00",
            "version": "vendor-v1",
        },
    )

    second = TestClient(create_app(settings=settings, accounts={"user": account}))
    query_headers = {"Authorization": f"Bearer {token}"}
    batch_id = imported.json()["data"]["batch_id"]
    listed = second.get("/api/v1/data/batches", headers=query_headers)
    quality = second.get(f"/api/v1/data/batches/{batch_id}/quality", headers=query_headers)
    pool = second.get("/api/v1/securities/pool?trade_date=2026-09-03", headers=query_headers)
    bars = second.get("/api/v1/data/bars?trade_date=2026-09-03", headers=query_headers)

    assert imported.status_code == 201
    assert imported.json()["data"]["file_hash"]
    assert imported.json()["data"]["version"] == "vendor-v1"
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["batch_id"] == batch_id
    assert quality.status_code == 200
    assert quality.json()["data"]["quality_status"] == "AVAILABLE"
    assert pool.status_code == 200
    assert pool.json()["data"]["items"][0]["symbol"] == "600000.SH"
    assert bars.status_code == 200
    assert bars.json()["data"]["total"] == 1
    assert bars.json()["data"]["items"][0]["raw_close"] == "10.100000"


def test_daily_bars_filter_by_symbol_and_paginate() -> None:
    client = make_client()
    response = client.get(
        "/api/v1/data/bars?trade_date=2026-09-03&symbol=600000.SH&page=1&page_size=1",
        headers=auth(client),
    )

    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["symbol"] == "600000.SH"
    assert response.json()["data"]["page_size"] == 1


def test_daily_bars_return_a_paged_empty_result_for_unknown_date() -> None:
    client = make_client()
    response = client.get(
        "/api/v1/data/bars?trade_date=2026-09-04&page=1&page_size=50",
        headers=auth(client),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "items": [],
        "page": 1,
        "page_size": 50,
        "total": 0,
    }


def test_invalid_csv_import_returns_quality_failure_and_no_pool_record(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'api-invalid-import.db'}"
    source = tmp_path / "invalid.csv"
    source.write_text(
        """symbol,trade_date,open,high,low,close,volume,amount,adjustment_factor
600000.SH,2026-09-03,0,10.20,9.90,10.10,10000,101000.00,1.0
""",
        encoding="utf-8",
    )
    owner_id = uuid4()
    account = LocalAccount(
        owner_id, "user", PasswordHasher().hash("pw"), frozenset({Role.USER, Role.ADMIN})
    )
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": database_url,
        }
    )
    client = TestClient(create_app(settings=settings, accounts={"user": account}))
    token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "api-invalid-login"},
        json={"username": "user", "password": "pw"},
    ).json()["data"]["access_token"]

    response = client.post(
        "/api/v1/data/imports",
        headers={
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": "api-invalid-1",
        },
        json={
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": str(source),
            "license_note": "licensed",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "BT_DATA_UNAVAILABLE"
    assert response.json()["error"]["details"][0]["quality"]["blocking"] is True
    batch_id = response.json()["error"]["details"][0]["batch_id"]
    quality = client.get(
        f"/api/v1/data/batches/{batch_id}/quality",
        headers={"Authorization": f"Bearer {token}"},
    )
    pool = client.get(
        "/api/v1/securities/pool?trade_date=2026-09-03",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert quality.status_code == 200
    assert quality.json()["data"]["quality_status"] == "UNAVAILABLE"
    assert pool.json()["data"]["total"] == 0


def test_quality_blocked_import_is_audited_once_across_an_idempotent_replay(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'api-import-audit.db'}"
    source = tmp_path / "invalid.csv"
    source.write_text(
        "symbol,trade_date,open,high,low,close,volume,amount,adjustment_factor\n"
        "600000.SH,2026-09-03,0,10.20,9.90,10.10,10000,101000.00,1.0\n",
        encoding="utf-8",
    )
    account = LocalAccount(uuid4(), "admin", PasswordHasher().hash("pw"), frozenset({Role.ADMIN}))
    app = create_app(
        settings=load_settings(
            {
                "APP_ENV": "development",
                "AUTH_SECRET_KEY": "development-only-secret-change-me",
                "DATABASE_URL": database_url,
            }
        ),
        accounts={"admin": account},
    )
    client = TestClient(app)
    token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "import-audit-login"},
        json={"username": "admin", "password": "pw"},
    ).json()["data"]["access_token"]
    request = {
        "headers": {"Authorization": f"Bearer {token}", "Idempotency-Key": "import-audit"},
        "json": {
            "source_name": "authorized-synthetic",
            "data_type": "DAILY_BAR",
            "file_location": str(source),
            "license_note": "authorized synthetic test data",
        },
    }

    first = client.post("/api/v1/data/imports", **request)
    replay = client.post("/api/v1/data/imports", **request)
    events, _ = app.state.audit_writer.page(1, 50)
    blocked = [event for event in events if event["action"] == "DATA_IMPORT"]

    assert first.status_code == 422
    assert replay.status_code == 422
    assert len(blocked) == 1
    assert blocked[0]["result"] == "BLOCKED"


def test_market_import_requires_admin_role(tmp_path) -> None:
    source = tmp_path / "bars.csv"
    source.write_text(
        "symbol,trade_date,open,high,low,close,volume,amount,adjustment_factor\n"
        "600000.SH,2026-09-03,10,10.2,9.9,10.1,10000,101000,1\n",
        encoding="utf-8",
    )
    client = make_client()

    response = client.post(
        "/api/v1/data/imports",
        headers=auth(client) | {"Idempotency-Key": "import-user-forbidden"},
        json={
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": str(source),
            "license_note": "licensed",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_market_import_requires_authentication() -> None:
    response = make_client().post(
        "/api/v1/data/imports",
        headers={"Idempotency-Key": "import-auth-required"},
        json={
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "authorized.csv",
            "license_note": "licensed",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_market_import_cookie_cannot_authenticate_a_write() -> None:
    client = make_client()
    client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "cookie-only-import-login"},
        json={"username": "user", "password": "pw"},
    )

    response = client.post(
        "/api/v1/data/imports",
        headers={"Idempotency-Key": "cookie-only-import"},
        json={
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "authorized.csv",
            "license_note": "licensed",
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


def test_published_strategy_list_is_limited_to_the_authenticated_owner() -> None:
    hasher = PasswordHasher()
    accounts = {
        "user": LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER})),
        "other": LocalAccount(uuid4(), "other", hasher.hash("pw"), frozenset({Role.USER})),
    }
    app = create_app(
        settings=load_settings({"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret"}),
        accounts=accounts,
    )
    user_strategy = app.state.repository.create_strategy(
        accounts["user"].id, {"name": "owned", "change_reason": "test", "parameters": {}}
    )
    app.state.repository.submit_strategy(
        user_strategy["strategy_version_id"],
        accounts["user"].id,
        "PUBLISH",
        "reviewed",
        allow_reviewer=True,
    )
    other_strategy = app.state.repository.create_strategy(
        accounts["other"].id, {"name": "other", "change_reason": "test", "parameters": {}}
    )
    app.state.repository.submit_strategy(
        other_strategy["strategy_version_id"],
        accounts["other"].id,
        "PUBLISH",
        "reviewed",
        allow_reviewer=True,
    )
    app.state.repository.create_strategy(
        accounts["user"].id, {"name": "draft", "change_reason": "test", "parameters": {}}
    )
    client = TestClient(app)

    response = client.get("/api/v1/strategies?status=PUBLISHED", headers=auth(client))

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert (
        response.json()["data"]["items"][0]["strategy_version_id"]
        == user_strategy["strategy_version_id"]
    )
    assert response.json()["data"]["items"][0]["status"] == "PUBLISHED"
