from fastapi.testclient import TestClient

from app.main import create_app


def test_health_is_dependency_neutral_and_propagates_request_id() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"X-Request-Id": "req_health_1"})

    assert response.status_code == 200
    assert response.headers["X-Request-Id"] == "req_health_1"
    assert response.json() == {
        "data": {"status": "ok"},
        "request_id": "req_health_1",
    }


def test_readiness_reports_named_checks_without_exposing_configuration() -> None:
    client = TestClient(
        create_app(readiness_checks={"database": lambda: True, "queue": lambda: False})
    )

    response = client.get("/api/v1/health/readiness")

    assert response.status_code == 503
    assert response.json()["data"] == {
        "status": "not_ready",
        "checks": {"database": "ok", "queue": "unavailable"},
    }
    assert "password" not in response.text.lower()
    assert "postgres" not in response.text.lower()


def test_mutating_routes_require_an_idempotency_key() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "missing", "password": "missing"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert response.headers["X-Request-Id"] == response.json()["request_id"]
