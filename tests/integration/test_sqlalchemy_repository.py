from uuid import uuid4

from sqlalchemy import create_engine, select

from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.repositories.research import SqlAlchemyResearchRepository
from app.infrastructure.repositories.runtime import (
    SqlAlchemyAuditWriter,
    SqlAlchemyIdempotencyStore,
    SqlAlchemyJobRunStore,
)


def test_batch_survives_a_new_repository_instance(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'research.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)

    owner_id = uuid4()
    first = SqlAlchemyResearchRepository.from_engine(engine)
    created = first.create_batch(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/sample.csv",
            "license_note": "licensed",
            "date_from": "2026-09-01",
            "date_to": "2026-09-03",
        },
    )

    second = SqlAlchemyResearchRepository.from_engine(engine)
    loaded = second.get_batch_quality(created["batch_id"], owner_id)

    assert loaded is not None
    assert loaded["batch_id"] == created["batch_id"]
    assert loaded["source_name"] == "licensed-csv"
    assert loaded["quality_status"] == "VALIDATING"
    assert loaded["content_hash"]
    assert loaded["available_at"]
    assert loaded["information_cutoff_at"]
    assert engine.connect().execute(select(models.DataBatch)).first() is not None


def test_development_app_defaults_to_durable_repository(tmp_path) -> None:
    from app.core.config import load_settings
    from app.main import create_app

    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
        }
    )

    app = create_app(settings=settings)

    assert isinstance(app.state.repository, SqlAlchemyResearchRepository)
    assert isinstance(app.state.audit_writer, SqlAlchemyAuditWriter)
    assert isinstance(app.state.idempotency_store, SqlAlchemyIdempotencyStore)
    assert isinstance(app.state.job_run_store, SqlAlchemyJobRunStore)


def test_published_strategy_and_backtest_run_survive_repository_restart(tmp_path) -> None:
    from datetime import date

    from sqlalchemy import create_engine

    database_url = f"sqlite:///{tmp_path / 'runs.db'}"
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    owner_id = uuid4()
    repository = SqlAlchemyResearchRepository.from_engine(engine)
    repository.initialize_local_default_versions()
    batch = repository.create_batch(
        owner_id,
        {
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/sample.csv",
            "license_note": "licensed",
            "date_to": "2026-09-03",
        },
    )
    strategy = repository.create_strategy(
        owner_id,
        {"name": "trend", "parameters": {"lookback": 20}, "change_reason": "initial"},
    )
    published = repository.submit_strategy(
        strategy["strategy_version_id"], owner_id, "PUBLISH", "reviewed", allow_reviewer=True
    )
    assert published is not None
    repository.set_batch_quality(batch["batch_id"], owner_id, "AVAILABLE")

    restarted = SqlAlchemyResearchRepository.from_engine(engine)
    assert restarted.dependencies_available(
        {
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
        }
    )
    run = restarted.create_run(
        owner_id,
        {
            "data_batch_id": batch["batch_id"],
            "strategy_version_id": strategy["strategy_version_id"],
            "cost_config_id": "cost_v1",
            "rule_config_id": "rule_v1",
            "start_date": date(2026, 1, 1).isoformat(),
            "end_date": date(2026, 1, 31).isoformat(),
            "train_end": date(2026, 1, 10).isoformat(),
            "valid_end": date(2026, 1, 20).isoformat(),
            "oos_start": date(2026, 1, 21).isoformat(),
            "benchmark_symbol": "000300.SH",
            "initial_equity": "20000.00",
            "mode": "BACKTEST",
        },
    )

    loaded = SqlAlchemyResearchRepository.from_engine(engine).get_run(run["run_id"], owner_id)
    assert loaded is not None
    assert loaded["status"] == "QUEUED"
    assert loaded["result_usable"] is False
    assert loaded["data_batch_id"] == batch["batch_id"]


def test_development_api_reads_batch_after_app_restart(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import load_settings
    from app.core.security import LocalAccount, PasswordHasher, Role
    from app.main import create_app

    owner_id = uuid4()
    account = LocalAccount(
        owner_id,
        "user",
        PasswordHasher().hash("pw"),
        frozenset({Role.USER}),
    )
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": f"sqlite:///{tmp_path / 'api.db'}",
        }
    )

    first = TestClient(create_app(settings=settings, accounts={"user": account}))
    login = first.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "login-restart"},
        json={"username": "user", "password": "pw"},
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    created = first.post(
        "/api/v1/data/batches",
        headers=headers | {"Idempotency-Key": "batch-restart"},
        json={
            "source_name": "licensed-csv",
            "data_type": "DAILY_BAR",
            "file_location": "data/sample.csv",
            "license_note": "licensed",
            "date_to": "2026-09-03",
        },
    )
    batch_id = created.json()["data"]["batch_id"]

    second = TestClient(create_app(settings=settings, accounts={"user": account}))
    loaded = second.get(f"/api/v1/data/batches/{batch_id}/quality", headers=headers)

    assert created.status_code == 201
    assert loaded.status_code == 200
    assert loaded.json()["data"]["batch_id"] == batch_id


def test_durable_read_models_do_not_return_in_memory_demo_state(tmp_path) -> None:
    from datetime import date

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(engine)
    repository = SqlAlchemyResearchRepository.from_engine(engine)

    assert repository.list_pool(date(2026, 9, 3), None, 1, 50)["total"] == 0
    assert repository.list_plans(date(2026, 9, 4), 1, 50)["total"] == 0
    assert repository.snapshots(str(uuid4()), 1, 50)["total"] == 0
    report = repository.daily_report(date(2026, 9, 3))
    assert report["status"] == "UNAVAILABLE"
    assert report["result_usable"] is False
    assert (
        repository.create_execution(
            {
                "plan_id": str(uuid4()),
                "quantity": 100,
                "unfilled_quantity": 0,
                "executed_at": "2026-09-04T09:30:00+08:00",
                "price": "10.00",
                "commission": "5.00",
                "stamp_tax": "0.00",
                "transfer_fee": "0.01",
                "other_fee": "0.00",
                "execution_type": "MANUAL_ENTRY",
                "note": "manual",
            },
            uuid4(),
        )
        is None
    )
    assert repository.confirm_plan("plan_001", uuid4(), "CONFIRM", 1, "manual review") is None
    assert repository.create_export("missing-report", uuid4())["status"] == "UNAVAILABLE"
