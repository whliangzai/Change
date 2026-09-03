"""Redis/RQ integration kept behind a small injectable boundary."""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core.errors import DependencyError


@dataclass(frozen=True, slots=True)
class QueueSettings:
    host: str = "127.0.0.1"
    port: int = 6379
    password: str | None = None
    queue_name: str = "default"

    @classmethod
    def from_env(cls) -> QueueSettings:
        return cls(
            host=os.getenv("REDIS_HOST", "127.0.0.1"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            queue_name=os.getenv("RQ_QUEUE", "default"),
        )


def redis_connection(settings: QueueSettings | None = None) -> Any:
    config = settings or QueueSettings.from_env()
    try:
        redis = importlib.import_module("redis")
    except ImportError as exc:
        raise DependencyError("Redis client is not installed") from exc
    return redis.Redis(
        host=config.host,
        port=config.port,
        password=config.password,
        decode_responses=False,
        socket_connect_timeout=5,
        socket_timeout=5,
    )


def rq_queue(settings: QueueSettings | None = None) -> Any:
    config = settings or QueueSettings.from_env()
    try:
        queue_module = importlib.import_module("rq")
    except ImportError as exc:
        raise DependencyError("RQ is not installed") from exc
    return queue_module.Queue(
        name=config.queue_name, connection=redis_connection(config), default_timeout=3600
    )


def enqueue(
    function: Callable[..., Any],
    *args: Any,
    settings: QueueSettings | None = None,
    job_id: str,
    **kwargs: Any,
) -> Any:
    """Enqueue a deterministic job id; duplicate enqueue is rejected by RQ."""
    queue = rq_queue(settings)
    try:
        return queue.enqueue(function, *args, job_id=job_id, **kwargs)
    except (ConnectionError, TimeoutError) as exc:
        raise DependencyError("Redis queue is unavailable") from exc


__all__ = ["QueueSettings", "enqueue", "redis_connection", "rq_queue"]
