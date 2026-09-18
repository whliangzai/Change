"""Redis/RQ integration kept behind a small injectable boundary."""

from __future__ import annotations

import importlib
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.core.config import ConfigurationError, _read_env_file
from app.core.errors import DependencyError


@dataclass(frozen=True, slots=True)
class QueueSettings:
    host: str = "127.0.0.1"
    port: int = 6379
    password: str | None = None
    queue_name: str = "default"
    db: int = 0

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> QueueSettings:
        values = _read_env_file() | dict(os.environ) if environ is None else environ
        try:
            port = int(values.get("REDIS_PORT", "6379"))
            db = int(values.get("REDIS_DB", "0"))
        except ValueError as exc:
            raise ConfigurationError("REDIS_PORT and REDIS_DB must be integers") from exc
        settings = cls(
            host=values.get("REDIS_HOST", "127.0.0.1").strip(),
            port=port,
            password=values.get("REDIS_PASSWORD") or None,
            queue_name=values.get("RQ_QUEUE", "default").strip(),
            db=db,
        )
        if not settings.host:
            raise ConfigurationError("REDIS_HOST must be configured")
        if not 1 <= settings.port <= 65535:
            raise ConfigurationError("REDIS_PORT must be between 1 and 65535")
        if settings.db < 0:
            raise ConfigurationError("REDIS_DB must be non-negative")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", settings.queue_name):
            raise ConfigurationError("RQ_QUEUE must contain 1-64 safe characters")
        return settings


def redis_connection(settings: QueueSettings | None = None) -> Any:
    config = settings or QueueSettings.from_env()
    try:
        redis = importlib.import_module("redis")
    except ImportError as exc:
        raise DependencyError("Redis client is not installed") from exc
    try:
        connection = redis.Redis(
            host=config.host,
            port=config.port,
            db=config.db,
            password=config.password,
            decode_responses=False,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        connection.ping()
        return connection
    except Exception as exc:
        # Include only the destination coordinates; never expose passwords or raw client errors.
        raise DependencyError(
            f"Redis is unavailable at {config.host}:{config.port}/{config.db}"
        ) from exc


def rq_queue(settings: QueueSettings | None = None) -> Any:
    config = settings or QueueSettings.from_env()
    try:
        queue_module = importlib.import_module("rq")
    except ImportError as exc:
        raise DependencyError("RQ is not installed") from exc
    try:
        return queue_module.Queue(
            name=config.queue_name, connection=redis_connection(config), default_timeout=3600
        )
    except DependencyError:
        raise
    except Exception as exc:
        raise DependencyError(
            f"RQ queue is unavailable at {config.host}:{config.port}/{config.db}"
        ) from exc


def enqueue(
    function: Callable[..., Any],
    *args: Any,
    settings: QueueSettings | None = None,
    job_id: str,
    **kwargs: Any,
) -> Any:
    """Enqueue a deterministic job id; duplicate enqueue is rejected by RQ."""
    config = settings or QueueSettings.from_env()
    queue = rq_queue(config)
    try:
        return queue.enqueue(function, *args, job_id=job_id, **kwargs)
    except Exception as exc:
        detail = str(exc).replace("\r", " ").replace("\n", " ").strip()[:160]
        if config.password:
            detail = detail.replace(config.password, "[REDACTED]")
        raise DependencyError(
            f"Redis queue is unavailable ({type(exc).__name__}): {detail}"
        ) from exc


__all__ = ["QueueSettings", "enqueue", "redis_connection", "rq_queue"]
