"""The research application must have no automatic broker/order-submit path."""

from app.core.config import load_settings
from app.main import create_app

TEST_SETTINGS = load_settings(
    {"APP_ENV": "test", "AUTH_SECRET_KEY": "test-secret-that-is-long-enough!"}
)


FORBIDDEN_PATH_PARTS = ("broker", "order-submit", "order_submit", "cancel-order", "cancel_order")


def test_public_routes_do_not_expose_broker_or_order_submission_operations() -> None:
    routes = {
        route.path
        for route in create_app(settings=TEST_SETTINGS).routes
        if hasattr(route, "path")
    }

    assert not any(
        any(forbidden in path.lower() for forbidden in FORBIDDEN_PATH_PARTS) for path in routes
    )


def test_openapi_does_not_advertise_a_broker_client_or_order_submission_operation() -> None:
    paths = create_app(settings=TEST_SETTINGS).openapi().get("paths", {})

    assert not any(
        any(forbidden in path.lower() for forbidden in FORBIDDEN_PATH_PARTS) for path in paths
    )
    operations = " ".join(str(operation).lower() for operation in paths.values())
    assert "order_submit" not in operations
    assert "broker" not in operations
