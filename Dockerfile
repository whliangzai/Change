FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=development \
    DATA_ROOT=/app/data

WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app
COPY pyproject.toml .env.example ./
RUN pip install --no-cache-dir \
    "fastapi>=0.115,<1.0" "uvicorn>=0.30,<1.0" "argon2-cffi>=23.1,<24.0" \
    "PyJWT>=2.9,<3.0" "redis>=5,<6" "rq>=2,<3" "pyarrow>=17,<20" \
    "pandas>=2.2,<3" "SQLAlchemy>=2.0,<3" "alembic>=1.13,<2" \
    "psycopg[binary]>=3.2,<4" "jinja2>=3.1,<4" "pydantic-settings>=2.6,<3" \
    "httpx>=0.27,<1.0" "akshare>=1.16,<2.0"
COPY app ./app
COPY scripts ./scripts
COPY docs ./docs
COPY README.md ./README.md
RUN mkdir -p /app/data /app/backups && chown -R app:app /app
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", "8000"]
