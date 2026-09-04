from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import load_settings
from app.core.security import PasswordHasher
from app.infrastructure.db import models
from app.infrastructure.db.session import make_engine, make_session_factory
from app.main import create_app


def test_database_account_can_login_after_application_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'database-auth.db'}"
    settings = load_settings(
        {
            "APP_ENV": "development",
            "AUTH_SECRET_KEY": "development-only-secret-change-me",
            "DATABASE_URL": database_url,
        }
    )
    create_app(settings=settings)
    factory = make_session_factory(make_engine(database_url))
    user_id = uuid4()
    with factory.begin() as session:
        roles = session.scalars(
            select(models.Role).where(models.Role.code.in_(("USER", "REVIEWER", "ADMIN")))
        ).all()
        assert {role.code for role in roles} == {"USER", "REVIEWER", "ADMIN"}
        session.add(
            models.UserAccount(
                id=user_id,
                username="database-admin",
                password_hash=PasswordHasher().hash("correct horse battery staple"),
                status="ACTIVE",
                created_at=datetime.now(UTC),
            )
        )
        session.add_all(models.UserRole(user_id=user_id, role_id=role.id) for role in roles)

    client = TestClient(create_app(settings=settings))
    response = client.post(
        "/api/v1/auth/login",
        headers={"Idempotency-Key": "database-account-login"},
        json={"username": "database-admin", "password": "correct horse battery staple"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["token_type"] == "bearer"
