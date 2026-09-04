"""Durable process-boundary stores used by the HTTP middleware."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.contracts import AuditEvent, AuditWriter, IdempotencyRequest, IdempotencyResult
from app.core.errors import IdempotencyConflictError, StateConflictError
from app.core.security import LocalAccount, Role
from app.infrastructure.db import models
from app.infrastructure.db.session import make_session_factory
from app.jobs.idempotency import JobRunRecord, JobRunStore


class SqlAlchemyIdempotencyStore:
    """Stores request reservations and completed response bytes transactionally."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemyIdempotencyStore:
        return cls(make_session_factory(engine))

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def acquire(self, request: IdempotencyRequest) -> IdempotencyResult | None:
        with self._session() as session:
            record = session.get(models.IdempotencyRecord, request.key)
            now = datetime.now(UTC)
            if record is None:
                record = models.IdempotencyRecord(
                    key=request.key,
                    request_hash=request.request_hash,
                    actor_id=request.actor_id,
                    path=request.path,
                    expires_at=request.expires_at,
                    status_code=None,
                    body=None,
                    content_type=None,
                    headers=[],
                    created_at=now,
                )
                session.add(record)
                return None
            expires_at = record.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= now:
                record.request_hash = request.request_hash
                record.actor_id = request.actor_id
                record.path = request.path
                record.expires_at = request.expires_at
                record.status_code = None
                record.body = None
                record.content_type = None
                record.headers = []
                return None
            if (
                record.request_hash != request.request_hash
                or record.actor_id != request.actor_id
                or record.path != request.path
            ):
                raise IdempotencyConflictError(
                    "Idempotency-Key was already used for another request"
                )
            if record.status_code is None or record.body is None:
                raise StateConflictError("A matching idempotent request is still in progress")
            return IdempotencyResult(
                record.status_code,
                record.body,
                record.content_type,
                tuple((item[0], item[1]) for item in record.headers),
            )

    def complete(self, request: IdempotencyRequest, result: IdempotencyResult) -> None:
        with self._session() as session:
            record = session.get(models.IdempotencyRecord, request.key)
            if record is None:
                raise StateConflictError("Idempotency-Key reservation is missing")
            if (
                record.request_hash != request.request_hash
                or record.actor_id != request.actor_id
                or record.path != request.path
            ):
                raise IdempotencyConflictError(
                    "Idempotency-Key does not match its original request"
                )
            record.status_code = result.status_code
            record.body = result.body
            record.content_type = result.content_type
            record.headers = [list(item) for item in result.headers]


class SqlAlchemySessionRegistry:
    """Restart-safe session state with explicit revocation timestamps."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemySessionRegistry:
        return cls(make_session_factory(engine))

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def create(self, user_id: UUID) -> UUID:
        session_id = uuid4()
        with self._session() as session:
            session.add(
                models.AuthSession(
                    session_id=session_id,
                    user_id=user_id,
                    created_at=datetime.now(UTC),
                )
            )
        return session_id

    def revoke(self, session_id: UUID) -> None:
        with self._session() as session:
            session.execute(
                update(models.AuthSession)
                .where(models.AuthSession.session_id == session_id)
                .values(revoked_at=datetime.now(UTC))
            )

    def revoke_user(self, user_id: UUID) -> None:
        with self._session() as session:
            session.execute(
                update(models.AuthSession)
                .where(
                    models.AuthSession.user_id == user_id,
                    models.AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=datetime.now(UTC))
            )

    def is_active(self, session_id: UUID, user_id: UUID) -> bool:
        with self._session() as session:
            record = session.get(models.AuthSession, session_id)
            return bool(record and record.user_id == user_id and record.revoked_at is None)

    def consume(self, session_id: UUID, user_id: UUID) -> bool:
        with self._session() as session:
            record = session.get(models.AuthSession, session_id)
            if record is None or record.user_id != user_id or record.revoked_at is not None:
                return False
            record.revoked_at = datetime.now(UTC)
            return True


class SqlAlchemyAccountDirectory:
    """Persistent local-account lookup and role assignment."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemyAccountDirectory:
        return cls(make_session_factory(engine))

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get(self, username: str) -> LocalAccount | None:
        with self._session() as session:
            account = session.scalar(
                select(models.UserAccount).where(models.UserAccount.username == username)
            )
            if account is None:
                return None
            codes = session.scalars(
                select(models.Role.code)
                .join(models.UserRole, models.UserRole.role_id == models.Role.id)
                .where(models.UserRole.user_id == account.id)
            ).all()
            try:
                roles = frozenset(Role(code) for code in codes)
            except ValueError:
                return None
            return LocalAccount(
                id=account.id,
                username=account.username,
                password_hash=account.password_hash,
                roles=roles,
                enabled=account.status == "ACTIVE",
            )

    def bootstrap(self, username: str, password_hash: str, roles: frozenset[Role]) -> bool:
        with self._session() as session:
            existing = session.scalar(
                select(models.UserAccount.id).where(models.UserAccount.username == username)
            )
            if existing is not None:
                return False
            role_models: list[models.Role] = []
            for role in roles:
                model = session.scalar(select(models.Role).where(models.Role.code == role.value))
                if model is None:
                    model = models.Role(id=uuid4(), code=role.value, name=role.value)
                    session.add(model)
                    session.flush()
                role_models.append(model)
            account = models.UserAccount(
                id=uuid4(),
                username=username,
                password_hash=password_hash,
                status="ACTIVE",
                created_at=datetime.now(UTC),
            )
            session.add(account)
            session.flush()
            session.add_all(
                models.UserRole(user_id=account.id, role_id=role.id) for role in role_models
            )
            return True

    def set_enabled(self, user_id: UUID, enabled: bool) -> None:
        with self._session() as session:
            account = session.get(models.UserAccount, user_id)
            if account is None:
                raise ValueError("account not found")
            account.status = "ACTIVE" if enabled else "DISABLED"

    def set_roles(self, user_id: UUID, roles: frozenset[Role]) -> None:
        with self._session() as session:
            if session.get(models.UserAccount, user_id) is None:
                raise ValueError("account not found")
            session.query(models.UserRole).filter(models.UserRole.user_id == user_id).delete()
            role_ids = session.scalars(
                select(models.Role.id).where(models.Role.code.in_([role.value for role in roles]))
            ).all()
            session.add_all(
                models.UserRole(user_id=user_id, role_id=role_id) for role_id in role_ids
            )


class SqlAlchemyJobRunStore(JobRunStore):
    """Durable job-run adapter matching the idempotent task protocol."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemyJobRunStore:
        return cls(make_session_factory(engine))

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def latest(self, idempotency_key: str) -> JobRunRecord | None:
        with self._session() as session:
            record = session.scalar(
                select(models.JobRun).where(models.JobRun.idempotency_key == idempotency_key)
            )
            return self._record(record) if record is not None else None

    def append(self, record: JobRunRecord) -> None:
        with self._session() as session:
            existing = session.scalar(
                select(models.JobRun).where(models.JobRun.idempotency_key == record.idempotency_key)
            )
            if existing is not None:
                if existing.status == "failed":
                    existing.id = UUID(record.run_id)
                    existing.status = record.status
                    existing.run_number = record.run_number
                    existing.attempt = record.attempt
                    existing.phase = record.phase
                    existing.value = cast(Any, self._json_value(record.value))
                    existing.error_summary = record.error_summary
                    existing.started_at = record.started_at
                    existing.ended_at = record.finished_at
                    return
                raise StateConflictError("A matching durable job is already recorded")
            session.add(self._model(record))

    def replace(self, record: JobRunRecord) -> None:
        identifier = UUID(record.run_id)
        with self._session() as session:
            stored = session.get(models.JobRun, identifier)
            if stored is None:
                raise KeyError(record.run_id)
            stored.status = record.status
            stored.run_number = record.run_number
            stored.attempt = record.attempt
            stored.phase = record.phase
            stored.value = cast(Any, self._json_value(record.value))
            stored.error_summary = record.error_summary
            stored.ended_at = record.finished_at

    @staticmethod
    def _model(record: JobRunRecord) -> models.JobRun:
        return models.JobRun(
            id=UUID(record.run_id),
            job_code=record.job_kind,
            business_date=record.business_date,
            idempotency_key=record.idempotency_key,
            status=record.status,
            run_number=record.run_number,
            attempt=record.attempt,
            phase=record.phase,
            value=SqlAlchemyJobRunStore._json_value(record.value),
            error_summary=record.error_summary,
            started_at=record.started_at,
            ended_at=record.finished_at,
        )

    @staticmethod
    def _record(record: models.JobRun) -> JobRunRecord:
        return JobRunRecord(
            run_id=str(record.id),
            idempotency_key=record.idempotency_key,
            job_kind=record.job_code,
            business_date=record.business_date,
            run_number=record.run_number or 1,
            attempt=record.attempt,
            phase=record.phase or "execute",
            status=record.status,  # type: ignore[arg-type]
            error_summary=record.error_summary,
            value=record.value,
            started_at=record.started_at,
            finished_at=record.ended_at,
        )

    @staticmethod
    def _json_value(value: object) -> object:
        if value is None or isinstance(value, (dict, list, str, int, float, bool)):
            return value
        return json.loads(json.dumps(value, default=str))


class SqlAlchemyAuditWriter(AuditWriter):
    """Append-only audit writer; no update or delete operation is exposed."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    @classmethod
    def from_engine(cls, engine: Engine) -> SqlAlchemyAuditWriter:
        return cls(make_session_factory(engine))

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def append(self, event: AuditEvent) -> None:
        with self._session() as session:
            actor_id = (
                event.actor_id
                if event.actor_id and session.get(models.UserAccount, event.actor_id)
                else None
            )
            session.add(
                models.AuditEvent(
                    occurred_at=event.occurred_at,
                    actor_user_id=actor_id,
                    actor_role=event.actor_roles[0] if event.actor_roles else None,
                    action=event.action,
                    object_type=event.object_type,
                    object_id=event.object_id or "",
                    before_digest=json.dumps(event.before_summary, sort_keys=True, default=str)
                    if event.before_summary is not None
                    else None,
                    after_digest=json.dumps(event.after_summary, sort_keys=True, default=str)
                    if event.after_summary is not None
                    else None,
                    request_id=event.request_id,
                    result=event.result,
                )
            )

    def count(self) -> int:
        with self._session() as session:
            return int(session.scalar(select(func.count()).select_from(models.AuditEvent)) or 0)

    def page(self, page: int, page_size: int) -> tuple[list[dict[str, object]], int]:
        with self._session() as session:
            total = int(session.scalar(select(func.count()).select_from(models.AuditEvent)) or 0)
            events = session.scalars(
                select(models.AuditEvent)
                .order_by(models.AuditEvent.occurred_at.desc(), models.AuditEvent.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            ).all()
            return (
                [
                    {
                        "occurred_at": event.occurred_at.isoformat(),
                        "actor_id": str(event.actor_user_id) if event.actor_user_id else None,
                        "actor_roles": [event.actor_role] if event.actor_role else [],
                        "action": event.action,
                        "object_type": event.object_type,
                        "object_id": event.object_id,
                        "request_id": event.request_id,
                        "result": event.result,
                    }
                    for event in events
                ],
                total,
            )


__all__ = [
    "SqlAlchemyAccountDirectory",
    "SqlAlchemyAuditWriter",
    "SqlAlchemyIdempotencyStore",
    "SqlAlchemyJobRunStore",
    "SqlAlchemySessionRegistry",
]
