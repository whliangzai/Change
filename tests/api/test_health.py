from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.main import create_app

TEST_SETTINGS = load_settings(
    {"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"}
)


def test_health_is_dependency_neutral_and_propagates_request_id() -> None:
    client = TestClient(create_app(settings=TEST_SETTINGS))

    response = client.get("/health", headers={"X-Request-Id": "req_health_1"})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "req_health_1"
    assert response.json() == {
        "data": {"status": "ok"},
        "request_id": "req_health_1",
    }


def test_readiness_reports_named_checks_without_exposing_configuration() -> None:
    client = TestClient(
        create_app(
            settings=TEST_SETTINGS,
            readiness_checks={"database": lambda: True, "queue": lambda: False},
        )
    )

    response = client.get("/api/v1/health/readiness")

    assert response.status_code == 503
    assert response.json()["data"] == {
        "status": "not_ready",
        "checks": {"database": "ok", "queue": "unavailable"},
    }
    assert "password" not in response.text.lower()
    assert "postgres" not in response.text.lower()


def test_non_test_readiness_probes_real_database_and_queue(monkeypatch) -> None:
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": "sqlite:///:memory:",
        }
    )
    monkeypatch.setattr("app.main.redis_connection", lambda _settings: object())

    response = TestClient(create_app(settings=settings)).get("/api/v1/health/readiness")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "ready",
        "checks": {"database": "ok", "queue": "ok"},
    }


def test_mutating_routes_require_an_idempotency_key() -> None:
    client = TestClient(create_app(settings=TEST_SETTINGS))

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "missing", "password": "missing"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.headers["X-Request-Id"] == response.json()["request_id"]


def test_whitespace_idempotency_key_is_rejected() -> None:
    client = TestClient(create_app(settings=TEST_SETTINGS))

    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "   "},
        json={"username": "missing", "password": "missing"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_framework_and_unhandled_errors_use_the_standard_error_envelope() -> None:
    app = create_app(settings=TEST_SETTINGS)

    @app.get("/http-error")
    def http_error() -> None:
        raise HTTPException(status_code=404, detail="hidden resource")

    @app.get("/unexpected-error")
    def unexpected_error() -> None:
        raise RuntimeError("secret=not-for-clients")

    client = TestClient(app, raise_server_exceptions=False)
    validation = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "login-1"},
        json={"username": "", "password": "password"},
    )
    http = client.get("/http-error")
    unexpected = client.get("/unexpected-error")

    assert validation.status_code == 400
    assert validation.json()["error"]["code"] == "VALIDATION_ERROR"
    assert http.status_code == 404
    assert http.json()["error"]["code"] == "NOT_FOUND"
    assert unexpected.status_code == 500
    assert unexpected.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "not-for-clients" not in unexpected.text
    assert unexpected.headers["X-Request-Id"].startswith("req_")


def test_invalid_or_overlong_request_id_is_replaced_with_a_generated_value() -> None:
    client = TestClient(create_app(settings=TEST_SETTINGS))

    response = client.get("/health", headers={"X-Request-Id": "invalid request id" * 10})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"].startswith("req_")
    assert response.headers["X-Request-Id"] == response.json()["request_id"]
