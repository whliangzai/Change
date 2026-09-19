from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.core.errors import DependencyError
from app.core.security import LocalAccount, PasswordHasher, Role
from app.jobs.tasks import import_ifind_data, import_tushare_data
from app.main import create_app


def _client(*, queue=None, ifind_full: bool = False) -> tuple[TestClient, str, object]:
    hasher = PasswordHasher()
    accounts = {
        "admin": LocalAccount(uuid4(), "admin", hasher.hash("pw"), frozenset({Role.ADMIN})),
        "user": LocalAccount(uuid4(), "user", hasher.hash("pw"), frozenset({Role.USER})),
    }
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "TUSHARE_ENABLED": "true",
            "TUSHARE_TOKEN": "deployment-tushare-token",
            "IFIND_ENABLED": "true",
            "IFIND_REFRESH_TOKEN": "deployment-ifind-token",
            "IFIND_FULL_ENABLED": "true" if ifind_full else "false",
        }
    )
    app = create_app(settings=settings, accounts=accounts)
    calls: list[tuple[object, tuple[object, ...], dict[str, object]]] = []

    def enqueue(function, *args, **kwargs):
        calls.append((function, args, kwargs))
        if queue is not None:
            raise queue
        return type("Job", (), {"id": kwargs["job_id"]})()

    app.state.tushare_enqueue = enqueue
    client = TestClient(app)
    token = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "provider-login"},
        json={"username": "admin", "password": "pw"},
    ).json()["data"]["access_token"]
    return client, token, app.state


def test_capabilities_are_admin_only_and_never_return_provider_secrets() -> None:
    client, token, _ = _client()
    response = client.get(
        "/api/v1/admin/data-imports/capabilities",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["tushare"]["full_open"] is False
    assert response.json()["data"]["ifind"]["full_open"] is False
    assert "deployment-tushare-token" not in response.text
    assert "deployment-ifind-token" not in response.text


def test_background_capabilities_use_execution_readiness_without_queue() -> None:
    hasher = PasswordHasher()
    accounts = {"admin": LocalAccount(uuid4(), "admin", hasher.hash("pw"), frozenset({Role.ADMIN}))}
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "PROVIDER_IMPORT_EXECUTION": "background",
        }
    )
    app = create_app(
        settings=settings,
        accounts=accounts,
        readiness_checks={
            "database": lambda: True,
            "queue": lambda: (_ for _ in ()).throw(RuntimeError()),
        },
    )

    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login",
            headers={"Idempotency-Key": "background-capability-login"},
            json={"username": "admin", "password": "pw"},
        ).json()["data"]["access_token"]
        response = client.get(
            "/api/v1/admin/data-imports/capabilities",
            headers={"Authorization": f"Bearer {token}"},
        )
        readiness = client.get("/api/v1/health/readiness")

    data = response.json()["data"]
    assert data["execution"] == {"mode": "background", "available": True}
    assert data["queue"] == {"required": False, "available": None}
    assert data["queue_available"] is False
    assert readiness.json()["data"]["checks"] == {
        "database": "ok",
        "provider_execution": "ok",
    }


@pytest.mark.parametrize("provider", ["tushare", "ifind"])
def test_background_submission_returns_before_task_finishes_and_reaches_terminal(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    hasher = PasswordHasher()
    accounts = {"admin": LocalAccount(uuid4(), "admin", hasher.hash("pw"), frozenset({Role.ADMIN}))}
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "PROVIDER_IMPORT_EXECUTION": "background",
            "TUSHARE_ENABLED": "true",
            "TUSHARE_TOKEN": "deployment-tushare-token",
            "IFIND_ENABLED": "true",
            "IFIND_REFRESH_TOKEN": "deployment-ifind-token",
        }
    )
    app = create_app(settings=settings, accounts=accounts)
    started = Event()
    release = Event()

    def run_in_background(business_date, scope, owner_id=None):
        started.set()
        release.wait(2)
        importer = import_tushare_data if provider == "tushare" else import_ifind_data
        return importer(
            date.fromisoformat(str(business_date)),
            lambda **_: {"quality_status": "AVAILABLE"},
            scope=scope,
            run_store=app.state.job_run_store,
            audit_writer=app.state.audit_writer,
        )

    monkeypatch.setattr(f"app.api.v1.admin.run_{provider}_import", run_in_background)
    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login",
            headers={"Idempotency-Key": "background-submit-login"},
            json={"username": "admin", "password": "pw"},
        ).json()["data"]["access_token"]
        response = client.post(
            f"/api/v1/admin/data-imports/{provider}/2025-01-02?scope=pilot",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "background-submit",
            },
        )
        assert response.status_code == 202
        assert started.wait(1)
        assert response.json()["data"]["status"] in {"QUEUED", "RUNNING"}
        release.set()
        job_id = response.json()["data"]["job_id"]
        for _ in range(100):
            detail = client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if detail.json()["data"]["status"] == "SUCCEEDED":
                break
        assert detail.json()["data"]["status"] == "SUCCEEDED"
        dispatch_events = [
            event
            for event in app.state.audit_writer.events
            if event.action == f"{provider.upper()}_IMPORT_DISPATCH"
        ]
        assert len(dispatch_events) == 1
        assert dispatch_events[0].after_summary["execution_mode"] == "background"


def test_background_entry_point_failure_does_not_leave_job_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hasher = PasswordHasher()
    accounts = {"admin": LocalAccount(uuid4(), "admin", hasher.hash("pw"), frozenset({Role.ADMIN}))}
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "PROVIDER_IMPORT_EXECUTION": "background",
            "TUSHARE_ENABLED": "true",
            "TUSHARE_TOKEN": "deployment-tushare-token",
        }
    )
    app = create_app(settings=settings, accounts=accounts)

    def fail_before_task_claim(*_args, **_kwargs):
        raise RuntimeError("token=must-not-be-persisted raw-provider-response")

    monkeypatch.setattr("app.api.v1.admin.run_tushare_import", fail_before_task_claim)
    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login",
            headers={"Idempotency-Key": "background-failure-login"},
            json={"username": "admin", "password": "pw"},
        ).json()["data"]["access_token"]
        submitted = client.post(
            "/api/v1/admin/data-imports/tushare/2025-01-04?scope=pilot",
            headers={
                "Authorization": f"Bearer {token}",
                "Idempotency-Key": "background-entry-failure",
            },
        )
        job_id = submitted.json()["data"]["job_id"]
        for _ in range(100):
            detail = client.get(
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if detail.json()["data"]["status"] == "FAILED":
                break

    assert submitted.status_code == 202
    assert detail.json()["data"]["status"] == "FAILED"
    assert detail.json()["data"]["error_summary"] == (
        "RuntimeError: background provider entry point failed"
    )
    assert "must-not-be-persisted" not in detail.text
    assert "raw-provider-response" not in detail.text


def test_background_startup_marks_abandoned_running_provider_job_retryable() -> None:
    settings = load_settings(
        {
            "APP_ENV": "test",
            "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!",
            "PROVIDER_IMPORT_EXECUTION": "background",
        }
    )
    app = create_app(settings=settings)
    queued = app.state.job_run_store.reserve_queued(
        "data-import",
        date(2025, 1, 2),
        "data-import:2025-01-02:tushare-pilot",
        phase="provider-import",
    )
    running = app.state.job_run_store.claim_queued(queued.idempotency_key, phase="provider-import")
    assert running is not None

    with TestClient(app):
        recovered = app.state.job_run_store.get(running.run_id)

    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error_summary is not None
    assert recovered.error_summary.startswith("DependencyError:")


def test_provider_submission_persists_queued_job_and_supports_filters() -> None:
    client, token, state = _client()
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "provider-submit"}
    response = client.post(
        "/api/v1/admin/data-imports/tushare/2025-01-02?scope=pilot", headers=headers
    )

    assert response.status_code == 202
    data = response.json()["data"]
    assert {data["status"], data["provider"], data["scope"]} == {"QUEUED", "tushare", "pilot"}
    detail = client.get(
        f"/api/v1/jobs/{data['job_id']}", headers={"Authorization": f"Bearer {token}"}
    )
    filtered = client.get(
        "/api/v1/jobs?provider=tushare&status=QUEUED&business_date=2025-01-02",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert detail.status_code == 200
    assert detail.json()["data"]["task_key"] == "data-import:2025-01-02:tushare-pilot"
    assert filtered.json()["data"]["total"] == 1
    assert len(state.job_run_store.records) == 1


def test_provider_enqueue_uses_rq_safe_uuid_job_id() -> None:
    client, token, state = _client()

    def enqueue(_function, *_args, **kwargs):
        assert ":" not in kwargs["job_id"]
        return type("Job", (), {"id": kwargs["job_id"]})()

    state.tushare_enqueue = enqueue
    response = client.post(
        "/api/v1/admin/data-imports/tushare/2025-01-03?scope=pilot",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "provider-submit-safe-id"},
    )

    assert response.status_code == 202
    assert ":" not in response.json()["data"]["job_id"]


def test_queue_failure_is_persisted_and_dependency_failure_can_be_retried() -> None:
    client, token, state = _client(
        queue=DependencyError("Redis is unavailable at redis.internal:6379/0")
    )
    failed = client.post(
        "/api/v1/admin/data-imports/tushare/2025-01-02?scope=pilot",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "queue-down"},
    )
    assert failed.status_code == 503
    records, total = state.job_run_store.page(1, 50, status="FAILED")
    assert total == 1
    assert (
        records[0].error_summary == "DependencyError: Redis is unavailable at redis.internal:6379/0"
    )

    state.tushare_enqueue = lambda function, *args, **kwargs: type(
        "Job", (), {"id": kwargs["job_id"]}
    )()
    retried = client.post(
        f"/api/v1/jobs/{records[0].run_id}/retry",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "queue-retry"},
    )

    assert retried.status_code == 202
    assert retried.json()["data"]["status"] == "QUEUED"
    assert retried.json()["data"]["task_key"] == records[0].idempotency_key
    assert state.job_run_store.page(1, 50)[1] == 2


def test_ifind_full_gate_is_closed_until_explicitly_enabled() -> None:
    client, token, _ = _client()
    response = client.post(
        "/api/v1/admin/data-imports/ifind/2025-01-02?scope=full",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "ifind-full"},
    )

    assert response.status_code == 503
    assert "full import is disabled" in response.json()["error"]["message"]


def test_stale_queued_provider_job_can_be_requeued_without_new_job_record() -> None:
    client, token, state = _client()
    record = state.job_run_store.reserve_queued(
        "data-import",
        date(2025, 1, 2),
        "data-import:2025-01-02:tushare-pilot",
        phase="provider-import",
        value={"provider": "tushare", "scope": "pilot"},
    )
    state.job_run_store.replace(
        replace(record, started_at=datetime.now(UTC) - timedelta(minutes=10))
    )

    response = client.post(
        f"/api/v1/jobs/{record.run_id}/retry",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "stale-requeue"},
    )

    assert response.status_code == 202
    payload = response.json()["data"]
    assert payload["status"] == "QUEUED"
    assert payload["job_id"] == record.run_id
    assert state.job_run_store.page(1, 50)[1] == 1


def test_stale_requeueing_provider_job_can_recover_after_process_crash() -> None:
    client, token, state = _client()
    record = state.job_run_store.reserve_queued(
        "data-import",
        date(2025, 1, 2),
        "data-import:2025-01-02:tushare-pilot",
        phase="requeueing",
        value={"provider": "tushare", "scope": "pilot"},
    )
    state.job_run_store.replace(
        replace(record, started_at=datetime.now(UTC) - timedelta(minutes=10))
    )

    response = client.post(
        f"/api/v1/jobs/{record.run_id}/retry",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "crashed-requeue"},
    )

    assert response.status_code == 202
    assert response.json()["data"]["job_id"] == record.run_id
