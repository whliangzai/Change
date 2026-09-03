from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.config import load_settings
from app.main import create_app

TEST_SETTINGS = load_settings(
    {"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"}
)


def test_idempotent_replay_preserves_safe_headers_and_current_request_id() -> None:
    app = create_app(settings=TEST_SETTINGS)

    @app.post("/exports")
    def create_export(request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=201,
            content={"data": {"export_id": "exp_1"}, "request_id": request.state.request_id},
            headers={
                "Location": "/exports/exp_1",
                "ETag": "export-v1",
                "Cache-Control": "private, max-age=60",
            },
        )

    client = TestClient(app)
    common_headers = {"Idempotency-Key": "export-1"}
    first = client.post(
        "/exports",
        headers=common_headers | {"X-Request-Id": "req_first"},
        json={"format": "csv"},
    )
    replay = client.post(
        "/exports",
        headers=common_headers | {"X-Request-Id": "req_replay"},
        json={"format": "csv"},
    )

    for response in (first, replay):
        assert response.status_code == 201
        assert response.headers["Location"] == "/exports/exp_1"
        assert response.headers["ETag"] == "export-v1"
        assert response.headers["Cache-Control"] == "private, max-age=60"
        assert response.json()["request_id"] == response.headers["X-Request-Id"]
    assert first.headers["X-Request-Id"] == "req_first"
    assert replay.headers["X-Request-Id"] == "req_replay"
