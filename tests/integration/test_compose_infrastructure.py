from pathlib import Path

COMPOSE = Path(__file__).parents[2] / "docker-compose.yml"


def test_compose_requires_runtime_secrets_and_database_url() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "AUTH_SECRET_KEY: ${AUTH_SECRET_KEY:?AUTH_SECRET_KEY is required}" in text
    assert "DATABASE_URL: ${DATABASE_URL:?DATABASE_URL is required}" in text
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}" in text


def test_compose_probes_application_dependencies_for_app_and_worker() -> None:
    text = COMPOSE.read_text(encoding="utf-8")

    assert "/api/v1/health/readiness" in text
    assert "redis_connection(QueueSettings.from_env())" in text
    assert 'command: ["python", "scripts/run_worker.py"]' in text
    assert "condition: service_healthy" in text
    assert "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}" in text
