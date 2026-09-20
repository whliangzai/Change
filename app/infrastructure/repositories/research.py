"""SQLAlchemy-backed persistence for research API resources."""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from hashlib import sha256
from statistics import median
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.common import InMemoryResearchRepository
from app.application.daily_flow_service import DailyFlowApplicationService, DailyFlowRequest
from app.core.errors import DataUnavailableError, StateConflictError
from app.domain.backtest.event_loop import BenchmarkUnavailableError, StatefulBacktestRunner
from app.domain.backtest.runner import BacktestConfig, BacktestResult
from app.domain.causality import as_utc
from app.domain.data.importer import canonical_content_hash
from app.domain.data.quality import DataQualityError, QualityGate
from app.domain.execution.costs import CostModel
from app.domain.execution.simulator import MarketBar
from app.domain.strategy.registry import (
    StrategyType,
    expand_parameters,
    get_strategy_definition,
)
from app.infrastructure.db import models
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import make_engine, make_session_factory

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(canonical.encode("utf-8")).hexdigest()


def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError):
        return None


def _stable_uuid(alias: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"a-share-quant:{alias}")


def _ordered_batch_ids(payload: dict[str, Any]) -> tuple[UUID, ...]:
    primary = _uuid(str(payload.get("data_batch_id") or ""))
    raw_values = payload.get("data_batch_ids")
    values = list(raw_values) if isinstance(raw_values, list) and raw_values else [primary]
    identifiers = tuple(_uuid(str(value)) for value in values)
    if primary is None or any(identifier is None for identifier in identifiers):
        raise ValueError("data batch identifiers must be UUIDs")
    typed = tuple(identifier for identifier in identifiers if identifier is not None)
    if typed[0] != primary:
        raise ValueError("data_batch_ids must start with data_batch_id")
    if len(set(typed)) != len(typed):
        raise ValueError("data_batch_ids cannot contain duplicates")
    return typed


def _cost_config_snapshot(cost: models.CostConfigVersion) -> dict[str, object]:
    return {
        "version": cost.version,
        "commission_rate": str(cost.commission_rate),
        "minimum_commission": str(cost.commission_min),
        "sell_stamp_duty_rate": str(cost.stamp_tax_sell_rate),
        "transfer_fee_rate": str(cost.transfer_rate),
        "regulatory_fee_rate": str(cost.regulatory_fee_rate),
        "handling_fee_rate": str(cost.handling_fee_rate),
        "commission_includes_regulatory": cost.commission_includes_regulatory,
        "commission_includes_handling": cost.commission_includes_handling,
        "slippage_buy": str(cost.slippage_buy),
        "slippage_sell": str(cost.slippage_sell),
        "execution_price_mode": cost.execution_price_mode,
        "partial_fill_mode": cost.partial_fill_mode,
        "config": dict(cost.config),
    }


def _cost_model(cost: models.CostConfigVersion) -> CostModel:
    return CostModel(
        commission_rate=cost.commission_rate,
        minimum_commission=cost.commission_min,
        sell_stamp_duty_rate=cost.stamp_tax_sell_rate,
        transfer_fee_rate=cost.transfer_rate,
        regulatory_fee_rate=cost.regulatory_fee_rate,
        handling_fee_rate=cost.handling_fee_rate,
        commission_includes_regulatory=cost.commission_includes_regulatory,
        commission_includes_handling=cost.commission_includes_handling,
        slippage_buy=cost.slippage_buy,
        slippage_sell=cost.slippage_sell,
        version=cost.version,
    )


class SqlAlchemyResearchRepository(InMemoryResearchRepository):
    """Durable adapter with the same API contract as the test repository.

    The inherited methods remain available for resources not yet connected to an
    application service. Batches created by the local runtime are persisted in SQL.
    """

    def __init__(self, factory: sessionmaker[Session], *, create_schema: bool = False) -> None:
        super().__init__()
        self._factory = factory
        if create_schema:
            bind = factory.kw["bind"]
            if isinstance(bind, Engine):
                Base.metadata.create_all(bind)

    @classmethod
    def from_engine(
        cls, engine: Engine, *, create_schema: bool = False
    ) -> SqlAlchemyResearchRepository:
        return cls(make_session_factory(engine), create_schema=create_schema)

    @classmethod
    def from_url(
        cls, database_url: str, *, create_schema: bool = False
    ) -> SqlAlchemyResearchRepository:
        return cls.from_engine(make_engine(database_url), create_schema=create_schema)

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

    def create_batch(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        content_hash = _hash_payload(payload)
        raw_date = payload.get("date_to") or payload.get("date_from") or date.today().isoformat()
        as_of_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
        now = datetime.now(UTC)
        with self._session() as session:
            source = session.scalar(
                select(models.DataSource).where(models.DataSource.name == payload["source_name"])
            )
            if source is None:
                source = models.DataSource(
                    name=payload["source_name"], kind=payload["data_type"], enabled=True
                )
                session.add(source)
                session.flush()
            batch = session.scalar(
                select(models.DataBatch).where(
                    models.DataBatch.source_id == source.id,
                    models.DataBatch.dataset_type == payload["data_type"],
                    models.DataBatch.as_of_date == as_of_date,
                    models.DataBatch.content_hash == content_hash,
                    models.DataBatch.owner_id == str(owner_id),
                )
            )
            if batch is None:
                batch = models.DataBatch(
                    source_id=source.id,
                    owner_id=str(owner_id),
                    dataset_type=payload["data_type"],
                    as_of_date=as_of_date,
                    available_at=now,
                    information_cutoff_at=now,
                    version=f"data-{content_hash[:16]}",
                    status="VALIDATING",
                    quality_summary={"file_location": payload["file_location"]},
                    content_hash=content_hash,
                    start_date=as_of_date,
                    end_date=as_of_date,
                    file_location=payload["file_location"],
                    license_note=payload.get("license_note"),
                )
                session.add(batch)
                session.flush()
            return self._batch_record(batch, source.name, payload)

    def find_daily_batch(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any] | None:
        content_hash = str(payload.get("content_hash") or "")
        if not content_hash:
            return None
        with self._session() as session:
            row = session.execute(
                select(models.DataBatch, models.DataSource)
                .join(models.DataSource, models.DataBatch.source_id == models.DataSource.id)
                .where(
                    models.DataBatch.owner_id == str(owner_id),
                    models.DataSource.name == payload["source_name"],
                    models.DataBatch.dataset_type == payload["data_type"],
                    models.DataBatch.content_hash == content_hash,
                )
                .order_by(models.DataBatch.id.desc())
            ).first()
            if row is None:
                return None
            batch, source = row
            return self._batch_record(batch, source.name, payload)

    def initialize_local_default_versions(self) -> dict[str, object]:
        """Create the documented local baseline versions once during environment bootstrap."""
        with self._session() as session:
            legacy = self._ensure_default_configs(session, "cost_v1", "rule_v1")
            current = self._ensure_default_configs(session, "cost_v1", "rule_v2")
            legacy_versions = legacy["created_versions"]
            current_versions = current["created_versions"]
            if not isinstance(legacy_versions, list) or not isinstance(current_versions, list):
                raise TypeError("default configuration versions must be a list")
            created_versions = [str(item) for item in legacy_versions]
            created_versions.extend(str(item) for item in current_versions)
            return {
                **legacy,
                "created": bool(created_versions),
                "created_versions": created_versions,
                "backtest_rule_version": "rule_v2",
            }

    def import_daily_bars(
        self,
        owner_id: UUID,
        payload: dict[str, Any],
        rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        """Persist a normalized authorized-bar batch only after a blocking quality gate."""
        normalized = [self._normalize_daily_bar_row(row) for row in rows]
        content_hash = str(payload.get("content_hash") or canonical_content_hash(normalized))
        file_hash = str(payload.get("file_hash") or content_hash)
        trade_dates = [row["trade_date"] for row in normalized if row["trade_date"] is not None]
        start_date = min(trade_dates) if trade_dates else date.today()
        end_date = max(trade_dates) if trade_dates else start_date
        as_of_date = end_date
        available_at = self._timestamp(payload.get("available_at")) or max(
            (row["available_at"] for row in normalized if row["available_at"] is not None),
            default=datetime.now(UTC),
        )
        information_cutoff_at = (
            self._timestamp(payload.get("information_cutoff_at")) or available_at
        )
        issues: list[dict[str, Any]] = []
        try:
            QualityGate().validate_daily_bars(normalized)
            QualityGate().validate_batch_provenance(
                as_of_date=as_of_date,
                available_at=available_at,
                information_cutoff_at=information_cutoff_at,
            )
        except DataQualityError as exc:
            issues = [
                {
                    "symbol": issue.symbol,
                    "trade_date": issue.trade_date.isoformat() if issue.trade_date else None,
                    "field": issue.field,
                    "message": issue.message,
                }
                for issue in exc.issues
            ]
        with self._session() as session:
            source = session.scalar(
                select(models.DataSource).where(models.DataSource.name == payload["source_name"])
            )
            if source is None:
                source = models.DataSource(
                    name=payload["source_name"], kind=payload["data_type"], enabled=True
                )
                session.add(source)
                session.flush()
            existing = session.scalar(
                select(models.DataBatch).where(
                    models.DataBatch.source_id == source.id,
                    models.DataBatch.dataset_type == payload["data_type"],
                    models.DataBatch.as_of_date == as_of_date,
                    models.DataBatch.content_hash == content_hash,
                    models.DataBatch.owner_id == str(owner_id),
                )
            )
            if existing is not None:
                raise StateConflictError(
                    "this market-data file has already been imported",
                    [{"batch_id": str(existing.id), "content_hash": content_hash}],
                )
            batch = models.DataBatch(
                id=uuid4(),
                source_id=source.id,
                owner_id=str(owner_id),
                dataset_type=payload["data_type"],
                as_of_date=as_of_date,
                available_at=available_at,
                information_cutoff_at=information_cutoff_at,
                version=str(payload.get("version") or f"data-{content_hash[:16]}"),
                status="UNAVAILABLE" if issues else "AVAILABLE",
                quality_summary={
                    "blocking": bool(issues),
                    "record_count": 0 if issues else len(normalized),
                    "issues": issues,
                    "file_location": payload.get("file_location"),
                    "mapping_version": payload.get("mapping_version", "authorized-file-v1"),
                    "adjustment_convention": payload.get(
                        "adjustment_convention", "RAW_TIMES_ADJUST_FACTOR"
                    ),
                    "field_convention": payload.get(
                        "field_convention", "DAILY_OHLCV_OPEN_CLOSE_LIMIT_V2"
                    ),
                },
                content_hash=content_hash,
                file_hash=file_hash,
                start_date=start_date,
                end_date=end_date,
                record_count=0 if issues else len(normalized),
                file_location=payload.get("file_location"),
                license_note=payload.get("license_note"),
            )
            session.add(batch)
            session.flush()
            if not issues:
                for row in normalized:
                    symbol = str(row["symbol"])
                    exchange = self._exchange(symbol)
                    security = session.scalar(
                        select(models.Security).where(
                            models.Security.exchange == exchange,
                            models.Security.symbol == symbol,
                        )
                    )
                    if security is None:
                        security = models.Security(
                            id=uuid4(),
                            symbol=symbol,
                            exchange=exchange,
                            security_type="COMMON",
                            list_date=date(1900, 1, 1),
                        )
                        session.add(security)
                        session.flush()
                    session.add(
                        models.DailyBar(
                            security_id=security.id,
                            trade_date=row["trade_date"],
                            raw_open=row["raw_open"],
                            raw_high=row["raw_high"],
                            raw_low=row["raw_low"],
                            raw_close=row["raw_close"],
                            adjusted_open=row["adjusted_open"],
                            adjusted_high=row["adjusted_high"],
                            adjusted_low=row["adjusted_low"],
                            adjusted_close=row["adjusted_close"],
                            volume=row["volume"],
                            amount=row["amount"],
                            adjust_factor=row["adjust_factor"],
                            available_at=row["available_at"],
                            open_limit_up=row["open_limit_up"],
                            open_limit_down=row["open_limit_down"],
                            close_limit_up=row["close_limit_up"],
                            close_limit_down=row["close_limit_down"],
                            limit_up=row["limit_up"],
                            limit_down=row["limit_down"],
                            data_batch_id=batch.id,
                        )
                    )
                    session.add(
                        models.SecurityStatusHistory(
                            security_id=security.id,
                            effective_date=row["trade_date"],
                            is_st=bool(row["is_st"]),
                            is_suspended=bool(row["is_suspended"]),
                            is_delist_period=bool(row["is_delist_period"]),
                            board="MAIN",
                            source_batch_id=batch.id,
                            observed_at=row["status_observed_at"],
                        )
                    )
            return self._batch_record(batch, source.name, payload)

    def import_provider_batch(
        self,
        owner_id: UUID,
        payload: dict[str, Any],
        rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        """Publish a provider batch with historical dimensions and provenance.

        Unlike the legacy file importer this path never invents listing dates or a
        board. Incomplete provider evidence is retained as an UNAVAILABLE batch.
        """
        normalized = [self._normalize_daily_bar_row(row) for row in rows]
        as_of_date = self._as_date(payload.get("date_to")) or date.today()
        available_at = self._timestamp(payload.get("available_at")) or datetime.now(UTC)
        cutoff = self._timestamp(payload.get("information_cutoff_at")) or available_at
        content_hash = str(payload.get("content_hash") or canonical_content_hash(normalized))
        quality_errors = list(payload.get("quality_errors") or [])
        try:
            QualityGate().validate_daily_bars(normalized)
            QualityGate().validate_batch_provenance(
                as_of_date=as_of_date, available_at=available_at, information_cutoff_at=cutoff
            )
        except DataQualityError as exc:
            quality_errors.extend(
                {
                    "symbol": issue.symbol,
                    "trade_date": issue.trade_date.isoformat() if issue.trade_date else None,
                    "field": issue.field,
                    "message": issue.message,
                }
                for issue in exc.issues
            )
        metadata = payload.get("provider_metadata") or payload.get("ifind_metadata") or {}
        source_name = str(payload.get("source_name") or "ifind_http")
        provider_name = source_name.split("_", 1)[0]
        quality_warnings = list(payload.get("quality_warnings") or [])
        with self._session() as session:
            source = session.scalar(
                select(models.DataSource).where(models.DataSource.name == source_name)
            )
            if source is None:
                source = models.DataSource(name=source_name, kind="HTTP", enabled=True)
                session.add(source)
                session.flush()
            version = str(payload.get("version") or f"{provider_name}-{content_hash[:16]}")
            existing = session.scalar(
                select(models.DataBatch).where(
                    models.DataBatch.source_id == source.id,
                    models.DataBatch.dataset_type == "DAILY_BAR",
                    models.DataBatch.as_of_date == as_of_date,
                    models.DataBatch.version == version,
                )
            )
            if existing is not None:
                return {**self._batch_record(existing, source.name, payload), "replayed": True}
            batch = models.DataBatch(
                id=uuid4(),
                source_id=source.id,
                owner_id=str(owner_id),
                dataset_type="DAILY_BAR",
                as_of_date=as_of_date,
                available_at=available_at,
                information_cutoff_at=cutoff,
                version=version,
                status="UNAVAILABLE"
                if quality_errors
                else "WARNING_AVAILABLE"
                if quality_warnings
                else "AVAILABLE",
                quality_summary={
                    "blocking": bool(quality_errors),
                    "record_count": 0 if quality_errors else len(normalized),
                    "issues": quality_errors,
                    "warnings": quality_warnings,
                    "provenance": payload.get("provenance", {}),
                    "actual_pulled_at": payload.get("actual_pulled_at"),
                    "mapping_version": payload.get("mapping_version"),
                    "adjustment_convention": payload.get("adjustment_convention"),
                    "field_convention": payload.get("field_convention"),
                },
                content_hash=content_hash,
                file_hash=payload.get("file_hash"),
                start_date=as_of_date,
                end_date=as_of_date,
                record_count=0 if quality_errors else len(normalized),
                file_location=payload.get("file_location"),
                license_note=str(payload.get("license_note") or f"{provider_name} HTTP API"),
            )
            session.add(batch)
            session.flush()
            if quality_errors:
                return self._batch_record(batch, source.name, payload)
            master_by_code = {
                self._ifind_code(row): row
                for row in metadata.get("security_master", [])
                if self._ifind_code(row)
            }
            metadata_errors = []
            for normalized_row in normalized:
                metadata_row = master_by_code.get(str(normalized_row["symbol"]), {})
                if (
                    self._as_date(
                        self._ifind_pick(metadata_row, "listed_at", "listedDate", "list_date")
                    )
                    is None
                ):
                    metadata_errors.append(
                        {
                            "symbol": str(normalized_row["symbol"]),
                            "field": "listed_at",
                            "message": f"{provider_name} listing date is required",
                        }
                    )
                if not self._ifind_pick(metadata_row, "board", "market"):
                    metadata_errors.append(
                        {
                            "symbol": str(normalized_row["symbol"]),
                            "field": "board",
                            "message": f"{provider_name} board is required",
                        }
                    )
            if metadata_errors:
                batch.status = "UNAVAILABLE"
                batch.record_count = 0
                batch.quality_summary = {
                    **(batch.quality_summary or {}),
                    "blocking": True,
                    "issues": metadata_errors,
                }
                return self._batch_record(batch, source.name, payload)
            for calendar_row in metadata.get("calendar", []):
                calendar_date = self._as_date(
                    self._ifind_pick(
                        calendar_row, "trade_date", "business_date", "tradeDate", "cal_date"
                    )
                )
                if calendar_date is None:
                    continue
                exchange = str(self._ifind_pick(calendar_row, "exchange", "market") or "SSE")
                existing_calendar = session.get(models.TradeCalendar, (exchange, calendar_date))
                if existing_calendar is None:
                    session.add(
                        models.TradeCalendar(
                            exchange=exchange,
                            trade_date=calendar_date,
                            is_open=bool(self._ifind_pick(calendar_row, "is_open", "isOpen")),
                        )
                    )
                else:
                    existing_calendar.is_open = bool(
                        self._ifind_pick(calendar_row, "is_open", "isOpen")
                    )
            industry_rows = metadata.get("industries", [])
            for row in normalized:
                symbol = str(row["symbol"])
                security = session.scalar(
                    select(models.Security).where(
                        models.Security.exchange == self._exchange(symbol),
                        models.Security.symbol == symbol,
                    )
                )
                master = master_by_code.get(symbol, {})
                if security is None:
                    listed = self._as_date(
                        master.get("listed_at")
                        or master.get("listedDate")
                        or master.get("list_date")
                    )
                    if listed is None:
                        quality_errors.append(
                            {
                                "symbol": symbol,
                                "field": "listed_at",
                                "message": f"{provider_name} listing date is required",
                            }
                        )
                        continue
                    security = models.Security(
                        id=uuid4(),
                        symbol=symbol,
                        exchange=self._exchange(symbol),
                        security_type="INDEX" if symbol in {"000300.SH", "000001.SH"} else "COMMON",
                        list_date=listed,
                        delist_date=self._as_date(
                            master.get("delisted_at")
                            or master.get("delistedDate")
                            or master.get("delist_date")
                        ),
                    )
                    session.add(security)
                    session.flush()
                elif master:
                    security.list_date = (
                        self._as_date(
                            master.get("listed_at")
                            or master.get("listedDate")
                            or master.get("list_date")
                        )
                        or security.list_date
                    )
                    security.delist_date = self._as_date(
                        master.get("delisted_at")
                        or master.get("delistedDate")
                        or master.get("delist_date")
                    )
                session.add(
                    models.DailyBar(
                        security_id=security.id,
                        trade_date=row["trade_date"],
                        raw_open=row["raw_open"],
                        raw_high=row["raw_high"],
                        raw_low=row["raw_low"],
                        raw_close=row["raw_close"],
                        adjusted_open=row["adjusted_open"],
                        adjusted_high=row["adjusted_high"],
                        adjusted_low=row["adjusted_low"],
                        adjusted_close=row["adjusted_close"],
                        volume=row["volume"],
                        amount=row["amount"],
                        adjust_factor=row["adjust_factor"],
                        available_at=row["available_at"],
                        open_limit_up=row["open_limit_up"],
                        open_limit_down=row["open_limit_down"],
                        close_limit_up=row["close_limit_up"],
                        close_limit_down=row["close_limit_down"],
                        limit_up=row["limit_up"],
                        limit_down=row["limit_down"],
                        data_batch_id=batch.id,
                    )
                )
                session.add(
                    models.AdjustmentFactor(
                        security_id=security.id,
                        effective_date=row["trade_date"],
                        factor=row["adjust_factor"],
                        factor_type="DEFAULT",
                        data_batch_id=batch.id,
                    )
                )
                session.add(
                    models.SecurityStatusHistory(
                        security_id=security.id,
                        effective_date=row["trade_date"],
                        is_st=bool(row["is_st"]),
                        is_suspended=bool(row["is_suspended"]),
                        is_delist_period=bool(row["is_delist_period"]),
                        board=self._normalized_board(self._ifind_pick(master, "board", "market")),
                        source_batch_id=batch.id,
                        observed_at=row["status_observed_at"],
                    )
                )
                for industry in (
                    item for item in industry_rows if self._ifind_code(item) == symbol
                ):
                    industry_code = self._ifind_pick(
                        industry, "industry_code", "industryCode", "index_code", "l3_code"
                    )
                    if industry_code:
                        effective_from = (
                            self._as_date(
                                self._ifind_pick(industry, "valid_from", "inDate", "in_date")
                            )
                            or row["trade_date"]
                        )
                        session.add(
                            models.IndustryMembershipHistory(
                                security_id=security.id,
                                industry_code=str(industry_code),
                                effective_from=effective_from,
                                effective_to=self._as_date(
                                    self._ifind_pick(industry, "valid_to", "outDate", "out_date")
                                ),
                                source_batch_id=batch.id,
                                observed_at=(
                                    self._timestamp(
                                        self._ifind_pick(
                                            industry,
                                            "observed_at",
                                            "available_at",
                                            "availableAt",
                                        )
                                    )
                                    or row["industry_observed_at"]
                                ),
                            )
                        )
            normalized_symbols = {str(row["symbol"]) for row in normalized}
            for status_row in metadata.get("statuses", []):
                symbol = self._ifind_code(status_row)
                if not symbol or symbol in normalized_symbols:
                    continue
                effective_date = self._as_date(
                    self._ifind_pick(status_row, "effective_date", "trade_date", "tradeDate")
                )
                master = master_by_code.get(symbol, {})
                if effective_date is None:
                    quality_errors.append(
                        {
                            "symbol": symbol,
                            "field": "effective_date",
                            "message": f"{provider_name} status effective date is required",
                        }
                    )
                    continue
                security = session.scalar(
                    select(models.Security).where(
                        models.Security.exchange == self._exchange(symbol),
                        models.Security.symbol == symbol,
                    )
                )
                listed_at = self._as_date(
                    self._ifind_pick(master, "listed_at", "listedDate", "list_date")
                )
                if security is None:
                    if listed_at is None:
                        quality_errors.append(
                            {
                                "symbol": symbol,
                                "field": "listed_at",
                                "message": f"{provider_name} listing date is required",
                            }
                        )
                        continue
                    security = models.Security(
                        id=uuid4(),
                        symbol=symbol,
                        exchange=self._exchange(symbol),
                        security_type="COMMON",
                        list_date=listed_at,
                        delist_date=self._as_date(
                            self._ifind_pick(master, "delisted_at", "delistedDate", "delist_date")
                        ),
                    )
                    session.add(security)
                    session.flush()
                elif listed_at is not None:
                    security.list_date = listed_at
                    security.delist_date = self._as_date(
                        self._ifind_pick(master, "delisted_at", "delistedDate", "delist_date")
                    )
                existing_status = session.get(
                    models.SecurityStatusHistory, (security.id, effective_date, batch.id)
                )
                if existing_status is None:
                    session.add(
                        models.SecurityStatusHistory(
                            security_id=security.id,
                            effective_date=effective_date,
                            is_st=bool(self._ifind_pick(status_row, "is_st", "st_flag")),
                            is_suspended=bool(
                                self._ifind_pick(status_row, "is_suspended", "suspended")
                            ),
                            is_delist_period=bool(
                                self._ifind_pick(
                                    status_row, "is_delist_period", "delisting_arrangement"
                                )
                            ),
                            board=self._normalized_board(
                                self._ifind_pick(master, "board", "market")
                            ),
                            source_batch_id=batch.id,
                            observed_at=(
                                self._timestamp(
                                    self._ifind_pick(
                                        status_row,
                                        "observed_at",
                                        "available_at",
                                        "availableAt",
                                    )
                                )
                                or available_at
                            ),
                        )
                    )
            if quality_errors:
                batch.status = "UNAVAILABLE"
                batch.quality_summary = {
                    **(batch.quality_summary or {}),
                    "blocking": True,
                    "issues": quality_errors,
                }
                batch.record_count = 0
            return self._batch_record(batch, source.name, payload)

    def import_ifind_batch(
        self,
        owner_id: UUID,
        payload: dict[str, Any],
        rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    ) -> dict[str, Any]:
        """Compatibility shim for already-deployed iFinD imports."""
        return self.import_provider_batch(owner_id, payload, rows)

    def list_batches(
        self, owner_id: UUID, status: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        with self._session() as session:
            query = (
                select(models.DataBatch, models.DataSource)
                .join(models.DataSource, models.DataBatch.source_id == models.DataSource.id)
                .where(models.DataBatch.owner_id == str(owner_id))
                .order_by(models.DataBatch.as_of_date.desc(), models.DataBatch.id.desc())
            )
            if status is not None:
                query = query.where(models.DataBatch.status == status)
            rows = [
                self._batch_record(batch, source.name, None)
                for batch, source in session.execute(query).all()
            ]
            return self._page(rows, page, page_size)

    def get_batch_quality(self, batch_id: str, owner_id: UUID) -> dict[str, Any] | None:
        identifier = _uuid(batch_id)
        if identifier is None:
            return None
        with self._session() as session:
            batch = session.scalar(
                select(models.DataBatch).where(
                    models.DataBatch.id == identifier,
                    models.DataBatch.owner_id == str(owner_id),
                )
            )
            if batch is None:
                return None
            source = session.get(models.DataSource, batch.source_id)
            return self._batch_record(
                batch, source.name if source else "unknown", batch.quality_summary
            )

    def list_daily_bars(
        self,
        trade_date: date,
        symbol: str | None,
        page: int,
        page_size: int,
        owner_id: UUID,
    ) -> dict[str, Any]:
        with self._session() as session:
            query = (
                select(models.DailyBar, models.Security, models.DataBatch)
                .join(models.Security, models.DailyBar.security_id == models.Security.id)
                .join(models.DataBatch, models.DataBatch.id == models.DailyBar.data_batch_id)
                .where(
                    models.DailyBar.trade_date == trade_date,
                    models.DataBatch.owner_id == str(owner_id),
                    models.DataBatch.status.in_(("AVAILABLE", "WARNING_AVAILABLE")),
                )
                .order_by(models.Security.symbol)
            )
            if symbol is not None:
                query = query.where(models.Security.symbol == symbol)
            rows = [
                self._daily_bar_record(bar, security, batch)
                for bar, security, batch in session.execute(query).all()
            ]
            return self._page(rows, page, page_size)

    def set_batch_quality(
        self,
        batch_id: str,
        owner_id: UUID,
        status: str,
        summary: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if status not in {"AVAILABLE", "WARNING_AVAILABLE", "UNAVAILABLE"}:
            raise ValueError(f"unsupported data quality status: {status}")
        identifier = _uuid(batch_id)
        if identifier is None:
            return None
        with self._session() as session:
            batch = session.scalar(
                select(models.DataBatch).where(
                    models.DataBatch.id == identifier,
                    models.DataBatch.owner_id == str(owner_id),
                )
            )
            if batch is None:
                return None
            batch.status = status
            batch.quality_summary = summary or batch.quality_summary or {}
            session.flush()
            source = session.get(models.DataSource, batch.source_id)
            return self._batch_record(
                batch, source.name if source else "unknown", batch.quality_summary
            )

    def create_strategy(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        with self._session() as session:
            self._ensure_user(session, owner_id)
            code = str(payload.get("code") or payload.get("name") or "strategy")
            strategy_type = str(payload.get("strategy_type") or StrategyType.STRONG_TREND.value)
            raw_parameters = dict(payload.get("parameters") or {})
            try:
                parameters = expand_parameters(strategy_type, raw_parameters)
            except ValueError:
                if "strategy_type" in payload:
                    raise
                parameters = raw_parameters
            versions = session.scalars(
                select(models.StrategyVersion).where(models.StrategyVersion.code == code)
            ).all()
            version = f"v{len(versions) + 1}"
            strategy = models.StrategyVersion(
                id=uuid4(),
                code=code,
                strategy_type=strategy_type,
                version=version,
                status="DRAFT",
                parameters=parameters,
                change_reason=str(payload.get("change_reason") or "initial version"),
                created_by=owner_id,
                published_at=None,
            )
            session.add(strategy)
            session.flush()
            return self._strategy_record(strategy)

    def submit_strategy(
        self,
        strategy_id: str,
        owner_id: UUID,
        decision: str,
        note: str,
        *,
        allow_reviewer: bool = False,
    ) -> dict[str, Any] | None:
        identifier = _uuid(strategy_id)
        if identifier is None:
            return None
        with self._session() as session:
            strategy = session.get(models.StrategyVersion, identifier)
            if strategy is None or (not allow_reviewer and strategy.created_by != owner_id):
                return None
            if strategy.status in {"PUBLISHED", "ARCHIVED"}:
                from app.core.errors import StateConflictError

                raise StateConflictError("published strategy versions cannot be edited")
            strategy.status = {
                "SUBMIT": "PENDING_REVIEW",
                "PUBLISH": "PUBLISHED",
                "REJECT": "DRAFT",
            }[decision]
            strategy.change_reason = note
            strategy.published_at = datetime.now(UTC) if decision == "PUBLISH" else None
            session.flush()
            return self._strategy_record(strategy)

    def strategy_diff(self, strategy_id: str, owner_id: UUID) -> dict[str, Any] | None:
        identifier = _uuid(strategy_id)
        if identifier is None:
            return None
        with self._session() as session:
            strategy = session.get(models.StrategyVersion, identifier)
            if strategy is None or strategy.created_by != owner_id:
                return None
            return {
                **self._strategy_record(strategy),
                "strategy_version_id": strategy_id,
                "base_version": None,
                "changes": strategy.parameters,
            }

    def list_strategies(
        self, owner_id: UUID, status: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        with self._session() as session:
            query = select(models.StrategyVersion).where(
                models.StrategyVersion.created_by == owner_id
            )
            if status is not None:
                query = query.where(models.StrategyVersion.status == status)
            strategies = session.scalars(query.order_by(models.StrategyVersion.id.desc())).all()
            return self._page(
                [self._strategy_record(strategy) for strategy in strategies], page, page_size
            )

    def dependencies_available(self, payload: dict[str, Any]) -> bool:
        try:
            batch_ids = _ordered_batch_ids(payload)
        except ValueError:
            return False
        strategy_id = _uuid(str(payload["strategy_version_id"]))
        if strategy_id is None:
            return False
        with self._session() as session:
            batches = session.scalars(
                select(models.DataBatch).where(models.DataBatch.id.in_(batch_ids))
            ).all()
            strategy = session.get(models.StrategyVersion, strategy_id)
            cost = self._resolve_config(
                session, models.CostConfigVersion, str(payload["cost_config_id"])
            )
            rule = self._resolve_config(
                session, models.RuleConfigVersion, str(payload["rule_config_id"])
            )
            return bool(
                len(batches) == len(batch_ids)
                and all(
                    batch.status in {"AVAILABLE", "WARNING_AVAILABLE"}
                    and (
                        payload.get("owner_id") is None
                        or batch.owner_id == str(payload["owner_id"])
                    )
                    for batch in batches
                )
                and strategy
                and strategy.status == "PUBLISHED"
                and (
                    payload.get("owner_id") is None
                    or strategy.created_by == _uuid(str(payload["owner_id"]))
                )
                and cost is not None
                and rule is not None
            )

    @staticmethod
    def _backtest_data_snapshot(
        session: Session,
        batches: tuple[models.DataBatch, ...],
        *,
        end_date: date,
    ) -> dict[str, object]:
        batch_ids = tuple(batch.id for batch in batches)
        status_records = session.execute(
            select(models.SecurityStatusHistory, models.Security.symbol)
            .join(
                models.Security,
                models.SecurityStatusHistory.security_id == models.Security.id,
            )
            .where(models.SecurityStatusHistory.source_batch_id.in_(batch_ids))
        ).all()
        status_payload = [
            {
                "source_batch_id": str(status.source_batch_id),
                "symbol": symbol,
                "effective_date": status.effective_date,
                "is_st": status.is_st,
                "is_suspended": status.is_suspended,
                "is_delist_period": status.is_delist_period,
                "board": status.board,
                "observed_at": as_utc(status.observed_at).isoformat(),
            }
            for status, symbol in status_records
        ]
        membership_records = session.execute(
            select(models.IndustryMembershipHistory, models.Security.symbol)
            .join(
                models.Security,
                models.IndustryMembershipHistory.security_id == models.Security.id,
            )
            .where(models.IndustryMembershipHistory.source_batch_id.in_(batch_ids))
        ).all()
        membership_payload = [
            {
                "source_batch_id": str(membership.source_batch_id),
                "symbol": symbol,
                "industry_code": membership.industry_code,
                "effective_from": membership.effective_from,
                "effective_to": membership.effective_to,
                "observed_at": as_utc(membership.observed_at).isoformat(),
            }
            for membership, symbol in membership_records
        ]
        security_records = session.scalars(
            select(models.Security)
            .join(models.DailyBar, models.DailyBar.security_id == models.Security.id)
            .where(models.DailyBar.data_batch_id.in_(batch_ids))
            .distinct()
        ).all()
        security_payload: list[dict[str, object]] = [
            {
                "security_id": str(security.id),
                "symbol": security.symbol,
                "exchange": security.exchange,
                "security_type": security.security_type,
                "list_date": security.list_date,
                "delist_date": security.delist_date,
            }
            for security in security_records
        ]
        calendar_records = session.scalars(
            select(models.TradeCalendar)
            .where(models.TradeCalendar.trade_date <= end_date)
            .order_by(models.TradeCalendar.exchange, models.TradeCalendar.trade_date)
        ).all()
        calendar_payload: list[dict[str, object]] = [
            {
                "exchange": record.exchange,
                "trade_date": record.trade_date,
                "is_open": record.is_open,
                "prev_trade_date": record.prev_trade_date,
                "next_trade_date": record.next_trade_date,
            }
            for record in calendar_records
        ]
        batch_payload = [
            {
                "ordinal": ordinal,
                "batch_id": str(batch.id),
                "source_id": str(batch.source_id),
                "dataset_type": batch.dataset_type,
                "as_of_date": batch.as_of_date.isoformat(),
                "start_date": batch.start_date.isoformat() if batch.start_date else None,
                "end_date": batch.end_date.isoformat() if batch.end_date else None,
                "available_at": as_utc(batch.available_at).isoformat(),
                "information_cutoff_at": as_utc(batch.information_cutoff_at).isoformat(),
                "version": batch.version,
                "status": batch.status,
                "content_hash": batch.content_hash,
                "file_hash": batch.file_hash,
                "record_count": batch.record_count,
                "mapping_version": (batch.quality_summary or {}).get("mapping_version"),
                "adjustment_convention": (batch.quality_summary or {}).get("adjustment_convention"),
                "field_convention": (batch.quality_summary or {}).get("field_convention"),
            }
            for ordinal, batch in enumerate(batches)
        ]
        return {
            "data_batches": batch_payload,
            "data_batch_set_hash": _hash_payload({"data_batches": batch_payload}),
            "status_history_hash": canonical_content_hash(status_payload),
            "industry_history_hash": canonical_content_hash(membership_payload),
            "security_master_hash": canonical_content_hash(security_payload),
            "trading_calendar_hash": canonical_content_hash(calendar_payload),
        }

    @staticmethod
    def _batch_set_error(
        session: Session,
        batches: tuple[models.DataBatch, ...],
        *,
        start_date: date,
        end_date: date,
    ) -> str | None:
        if not batches:
            return "data batch set is empty"
        if len({batch.source_id for batch in batches}) != 1:
            return "data batches must use the same supplier"
        if len({batch.dataset_type for batch in batches}) != 1:
            return "data batches must use the same dataset type"
        batch_dates = tuple(batch.as_of_date for batch in batches)
        if any(
            current <= previous
            for previous, current in zip(batch_dates, batch_dates[1:], strict=False)
        ):
            return "data batches must be ordered by strictly increasing dates"
        conventions = {
            (
                (batch.quality_summary or {}).get("mapping_version"),
                (batch.quality_summary or {}).get("adjustment_convention"),
                (batch.quality_summary or {}).get("field_convention"),
            )
            for batch in batches
        }
        if len(conventions) != 1 or any(value is None for value in next(iter(conventions))):
            return "data batches have incompatible or unproven field conventions"
        selected_batch_ids = tuple(batch.id for batch in batches)
        overlapping_market_dates = session.scalars(
            select(models.DailyBar.trade_date)
            .where(
                models.DailyBar.data_batch_id.in_(selected_batch_ids),
                models.DailyBar.trade_date >= start_date,
                models.DailyBar.trade_date <= end_date,
            )
            .group_by(models.DailyBar.trade_date)
            .having(func.count(func.distinct(models.DailyBar.data_batch_id)) > 1)
        ).all()
        if overlapping_market_dates:
            return "each market date must resolve to exactly one selected batch version"
        unresolved_industry_dates = session.scalars(
            select(models.IndustryMembershipHistory.effective_from)
            .where(
                models.IndustryMembershipHistory.source_batch_id.in_(selected_batch_ids),
                models.IndustryMembershipHistory.effective_from <= end_date,
            )
            .group_by(
                models.IndustryMembershipHistory.security_id,
                models.IndustryMembershipHistory.effective_from,
                models.IndustryMembershipHistory.source_batch_id,
            )
            .having(func.count(func.distinct(models.IndustryMembershipHistory.industry_code)) > 1)
        ).all()
        if unresolved_industry_dates:
            return "industry history has an unresolved selected-batch tie"
        open_dates = set(
            session.scalars(
                select(models.TradeCalendar.trade_date)
                .where(
                    models.TradeCalendar.is_open.is_(True),
                    models.TradeCalendar.trade_date >= start_date,
                    models.TradeCalendar.trade_date <= end_date,
                )
                .distinct()
            ).all()
        )
        if not open_dates:
            return "authoritative trading calendar is unavailable"
        covered_dates = {
            trade_date
            for trade_date in open_dates
            if any(
                (batch.start_date or batch.as_of_date)
                <= trade_date
                <= (batch.end_date or batch.as_of_date)
                for batch in batches
            )
        }
        if covered_dates != open_dates:
            return "data batch set does not cover every requested trading date"
        return None

    def validate_backtest(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate a durable backtest without creating configs, runs, or artifacts."""
        try:
            batch_ids = _ordered_batch_ids(payload)
            strategy_id = _uuid(str(payload["strategy_version_id"]))
            start = date.fromisoformat(str(payload["start_date"]))
            end = date.fromisoformat(str(payload["end_date"]))
            train_end = date.fromisoformat(str(payload["train_end"]))
            valid_end = date.fromisoformat(str(payload["valid_end"]))
            oos_start = date.fromisoformat(str(payload["oos_start"]))
        except (KeyError, TypeError, ValueError):
            return {"available": False, "reason": "invalid backtest identifiers or dates"}
        if strategy_id is None or not start <= train_end < valid_end < oos_start <= end:
            return {"available": False, "reason": "invalid backtest date split"}
        with self._session() as session:
            batches_by_id = {
                batch.id: batch
                for batch in session.scalars(
                    select(models.DataBatch).where(
                        models.DataBatch.id.in_(batch_ids),
                        models.DataBatch.owner_id == str(owner_id),
                    )
                ).all()
            }
            batches = tuple(batches_by_id.get(batch_id) for batch_id in batch_ids)
            strategy = session.scalar(
                select(models.StrategyVersion).where(
                    models.StrategyVersion.id == strategy_id,
                    models.StrategyVersion.created_by == owner_id,
                )
            )
            cost = self._resolve_config(
                session, models.CostConfigVersion, str(payload["cost_config_id"])
            )
            rule = self._resolve_config(
                session, models.RuleConfigVersion, str(payload["rule_config_id"])
            )
            if (
                any(batch is None for batch in batches)
                or any(
                    batch is not None and batch.status not in {"AVAILABLE", "WARNING_AVAILABLE"}
                    for batch in batches
                )
                or strategy is None
                or strategy.status != "PUBLISHED"
            ):
                return {
                    "available": False,
                    "reason": "backtest batch or published strategy is unavailable",
                }
            if cost is None or rule is None:
                return {
                    "available": False,
                    "reason": "backtest cost or rule configuration is unavailable",
                }
            typed_batches = tuple(batch for batch in batches if batch is not None)
            batch_set_error = self._batch_set_error(
                session,
                typed_batches,
                start_date=start,
                end_date=end,
            )
            if batch_set_error is not None:
                return {"available": False, "reason": batch_set_error}
            maximum_cutoff = max(typed_batches, key=lambda item: as_utc(item.information_cutoff_at))
            cutoff = (
                self._timestamp(payload.get("information_cutoff_at"))
                or maximum_cutoff.information_cutoff_at
            )
            if as_utc(cutoff) > as_utc(maximum_cutoff.information_cutoff_at):
                return {
                    "available": False,
                    "reason": "information cutoff is later than the data batch cutoff",
                }
            if any(as_utc(batch.available_at) > as_utc(cutoff) for batch in typed_batches):
                return {
                    "available": False,
                    "reason": "one or more data batches are not available by the information cutoff",
                }
            rows = session.execute(
                select(models.DailyBar, models.Security)
                .join(models.Security, models.DailyBar.security_id == models.Security.id)
                .where(
                    models.DailyBar.data_batch_id.in_(batch_ids),
                    models.DailyBar.trade_date <= end,
                )
            ).all()
            available = [
                (bar, security)
                for bar, security in rows
                if bar.available_at is not None and as_utc(bar.available_at) <= as_utc(cutoff)
            ]
            requested = [
                (bar, security) for bar, security in available if start <= bar.trade_date <= end
            ]
            if not requested:
                return {
                    "available": False,
                    "reason": "no bars are available before the information cutoff",
                }
            open_dates = tuple(
                session.scalars(
                    select(models.TradeCalendar.trade_date)
                    .where(
                        models.TradeCalendar.is_open.is_(True),
                        models.TradeCalendar.trade_date >= start,
                        models.TradeCalendar.trade_date <= end,
                    )
                    .distinct()
                    .order_by(models.TradeCalendar.trade_date)
                ).all()
            )
            benchmark_dates = {
                bar.trade_date
                for bar, security in requested
                if security.symbol == str(payload["benchmark_symbol"])
            }
            if not open_dates or any(value not in benchmark_dates for value in open_dates):
                return {"available": False, "reason": "benchmark market snapshot is unavailable"}
            return {
                "available": True,
                "data_batch_id": str(batch_ids[0]),
                "data_batch_ids": [str(batch_id) for batch_id in batch_ids],
                "data_version": _hash_payload(
                    {"content_hashes": [batch.content_hash for batch in typed_batches]}
                ),
                "bar_count": len(requested),
                "information_cutoff_at": cutoff.isoformat(),
            }

    def create_run(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        batch_ids = _ordered_batch_ids(payload)
        strategy_id = _uuid(str(payload["strategy_version_id"]))
        if strategy_id is None:
            raise ValueError("data_batch_id and strategy_version_id must be UUIDs")
        start_date = date.fromisoformat(str(payload["start_date"]))
        end_date = date.fromisoformat(str(payload["end_date"]))
        train_end = date.fromisoformat(str(payload["train_end"]))
        valid_end = date.fromisoformat(str(payload["valid_end"]))
        oos_start = date.fromisoformat(str(payload["oos_start"]))
        if not start_date <= train_end < valid_end < oos_start <= end_date:
            raise ValueError("invalid train/validation/out-of-sample date split")
        with self._session() as session:
            self._ensure_user(session, owner_id)
            cost_id = self._resolve_config_id(
                session, models.CostConfigVersion, str(payload["cost_config_id"])
            )
            rule_id = self._resolve_config_id(
                session, models.RuleConfigVersion, str(payload["rule_config_id"])
            )
            cost = session.get(models.CostConfigVersion, cost_id)
            rule = session.get(models.RuleConfigVersion, rule_id)
            batches_by_id = {
                batch.id: batch
                for batch in session.scalars(
                    select(models.DataBatch).where(
                        models.DataBatch.id.in_(batch_ids),
                        models.DataBatch.owner_id == str(owner_id),
                        models.DataBatch.status.in_(("AVAILABLE", "WARNING_AVAILABLE")),
                    )
                ).all()
            }
            batches = tuple(batches_by_id.get(batch_id) for batch_id in batch_ids)
            strategy = session.scalar(
                select(models.StrategyVersion).where(
                    models.StrategyVersion.id == strategy_id,
                    models.StrategyVersion.created_by == owner_id,
                    models.StrategyVersion.status == "PUBLISHED",
                )
            )
            if (
                any(batch is None for batch in batches)
                or strategy is None
                or cost is None
                or rule is None
                or rule.status != "PUBLISHED"
            ):
                raise StateConflictError("backtest prerequisites are unavailable")
            if rule.config.get("baseline") != "engine-v2":
                raise StateConflictError("engine-v2 backtests require an engine-v2 rule version")
            definition = get_strategy_definition(strategy.strategy_type)
            secondary_benchmark = str(payload.get("secondary_benchmark_symbol") or "000001.SH")
            universe_benchmark_enabled = bool(payload.get("universe_benchmark_enabled", True))
            typed_batches = tuple(batch for batch in batches if batch is not None)
            batch_set_error = self._batch_set_error(
                session,
                typed_batches,
                start_date=start_date,
                end_date=end_date,
            )
            if batch_set_error is not None:
                raise StateConflictError(batch_set_error)
            maximum_cutoff = max(typed_batches, key=lambda item: as_utc(item.information_cutoff_at))
            cutoff = (
                self._timestamp(payload.get("information_cutoff_at"))
                or maximum_cutoff.information_cutoff_at
            )
            if as_utc(cutoff) > as_utc(maximum_cutoff.information_cutoff_at) or any(
                as_utc(batch.available_at) > as_utc(cutoff) for batch in typed_batches
            ):
                raise StateConflictError(
                    "one or more data batches are not available by the information cutoff"
                )
            data_snapshot = self._backtest_data_snapshot(session, typed_batches, end_date=end_date)
            cost_snapshot = _cost_config_snapshot(cost)
            config_snapshot = {
                **dict(payload),
                "data_batch_id": str(batch_ids[0]),
                "data_batch_ids": [str(batch_id) for batch_id in batch_ids],
                **data_snapshot,
                "information_cutoff_at": as_utc(cutoff).isoformat(),
                "strategy_type": strategy.strategy_type,
                "effective_parameters": dict(strategy.parameters),
                "strategy_implementation_version": definition.implementation_version,
                "rule_version": rule.version,
                "rule_config": dict(rule.config),
                "rule_config_hash": _hash_payload(dict(rule.config)),
                "cost_config": cost_snapshot,
                "cost_config_hash": _hash_payload(cost_snapshot),
                "secondary_benchmark_symbol": secondary_benchmark,
                "universe_benchmark_enabled": universe_benchmark_enabled,
                "engine_version": "engine-v2",
            }
            requested_run_no = str(payload.get("run_no") or "")
            if requested_run_no:
                existing = session.scalar(
                    select(models.BacktestRun).where(models.BacktestRun.run_no == requested_run_no)
                )
                if existing is not None:
                    if existing.created_by != owner_id:
                        raise StateConflictError("backtest run identifier belongs to another owner")
                    return self._run_record(existing)
            run = models.BacktestRun(
                id=uuid4(),
                run_no=requested_run_no or f"run_{uuid4().hex[:12]}",
                data_batch_id=batch_ids[0],
                strategy_version_id=strategy_id,
                cost_config_id=cost_id,
                rule_config_id=rule_id,
                start_date=start_date,
                end_date=end_date,
                train_end=train_end,
                valid_end=valid_end,
                oos_start=oos_start,
                benchmark_symbol=str(payload["benchmark_symbol"]),
                secondary_benchmark_symbol=secondary_benchmark,
                universe_benchmark_enabled=universe_benchmark_enabled,
                execution_price_mode="NEXT_OPEN_ADJUSTED",
                initial_equity=payload["initial_equity"],
                config_snapshot=config_snapshot,
                status="QUEUED",
                result_usable=False,
                created_by=owner_id,
                created_at=datetime.now(UTC),
            )
            session.add(run)
            session.flush()
            return self._run_record(run)

    def run_daily_flow(self, owner_id: UUID, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute and persist the real local daily research workflow once."""
        batch_id = _uuid(str(payload.get("data_batch_id")))
        strategy_id = _uuid(str(payload.get("strategy_version_id")))
        if batch_id is None or strategy_id is None:
            raise ValueError("data_batch_id and strategy_version_id must be UUIDs")
        as_of_date = date.fromisoformat(str(payload["as_of_date"]))
        idempotency_key = str(payload.get("idempotency_key") or f"daily:{as_of_date.isoformat()}")
        run_no = f"daily_{sha256(f'{owner_id}:{idempotency_key}'.encode()).hexdigest()[:12]}"
        with self._session() as session:
            existing = session.scalar(
                select(models.BacktestRun).where(
                    models.BacktestRun.run_no == run_no,
                    models.BacktestRun.created_by == owner_id,
                )
            )
            if existing is not None:
                return self._daily_flow_record(existing)
            batch = session.scalar(
                select(models.DataBatch).where(
                    models.DataBatch.id == batch_id,
                    models.DataBatch.owner_id == str(owner_id),
                )
            )
            strategy = session.scalar(
                select(models.StrategyVersion).where(
                    models.StrategyVersion.id == strategy_id,
                    models.StrategyVersion.created_by == owner_id,
                    models.StrategyVersion.status == "PUBLISHED",
                )
            )
            if batch is None or strategy is None:
                raise StateConflictError("daily flow prerequisites are unavailable")
            if strategy.strategy_type != StrategyType.STRONG_TREND.value:
                raise StateConflictError(
                    f"strategy {strategy.strategy_type} is not approved for daily flow"
                )
            cost = self._resolve_config(
                session, models.CostConfigVersion, str(payload.get("cost_config_id", "cost_v1"))
            )
            rule = self._resolve_config(
                session, models.RuleConfigVersion, str(payload.get("rule_config_id", "rule_v1"))
            )
            if (
                batch.status not in {"AVAILABLE", "WARNING_AVAILABLE"}
                or cost is None
                or rule is None
            ):
                raise StateConflictError(
                    "daily flow data or versioned configuration is unavailable"
                )
            information_cutoff_at = (
                self._timestamp(payload.get("information_cutoff_at")) or batch.information_cutoff_at
            )
            if as_utc(information_cutoff_at) > as_utc(batch.information_cutoff_at):
                raise StateConflictError("information cutoff is later than the data batch cutoff")
            rows = session.execute(
                select(models.DailyBar, models.Security)
                .join(models.Security, models.DailyBar.security_id == models.Security.id)
                .where(models.DailyBar.data_batch_id == batch.id)
                .order_by(models.DailyBar.trade_date, models.Security.symbol)
            ).all()
            statuses = session.scalars(
                select(models.SecurityStatusHistory).where(
                    models.SecurityStatusHistory.source_batch_id == batch.id,
                    models.SecurityStatusHistory.observed_at <= information_cutoff_at,
                )
            ).all()
            status_by_key = {
                (status.security_id, status.effective_date): status for status in statuses
            }
            bars = tuple(
                MarketBar(
                    symbol=security.symbol,
                    trade_date=bar.trade_date,
                    open=bar.adjusted_open,
                    low=bar.adjusted_low,
                    high=bar.adjusted_high,
                    close=bar.adjusted_close,
                    is_suspended=self._status_is_suspended(
                        status_by_key.get((security.id, bar.trade_date))
                    ),
                    limit_up=bool(bar.open_limit_up),
                    limit_down=bool(bar.open_limit_down),
                    limit_status_known=(
                        bar.open_limit_up is not None and bar.open_limit_down is not None
                    ),
                    close_limit_up=bar.close_limit_up,
                    close_limit_down=bar.close_limit_down,
                    amount=bar.amount,
                    available_at=bar.available_at,
                    is_st=bool(
                        status_by_key.get((security.id, bar.trade_date))
                        and status_by_key[(security.id, bar.trade_date)].is_st
                    ),
                    is_delisted=bool(
                        status_by_key.get((security.id, bar.trade_date))
                        and status_by_key[(security.id, bar.trade_date)].is_delist_period
                    ),
                    list_date=security.list_date,
                )
                for bar, security in rows
            )
            visible_bars = [
                bar
                for bar in bars
                if bar.trade_date <= as_of_date
                and bar.available_at is not None
                and as_utc(bar.available_at) <= as_utc(information_cutoff_at)
            ]
            if not any(
                bar.symbol == str(payload.get("benchmark_symbol", "000300.SH"))
                and bar.trade_date == as_of_date
                for bar in visible_bars
            ):
                raise DataUnavailableError("benchmark market snapshot is unavailable")
            benchmark_dates = {
                bar.trade_date
                for bar in visible_bars
                if bar.symbol == str(payload.get("benchmark_symbol", "000300.SH"))
            }
            if len(benchmark_dates) < 21:
                raise DataUnavailableError(
                    "daily flow requires at least 21 visible benchmark trading days"
                )
            initial_equity = Decimal(str(payload.get("initial_equity", "20000.00")))
            max_investment_ratio = Decimal(str(payload.get("max_investment_ratio", "0.70")))
            request = DailyFlowRequest(
                owner_id=owner_id,
                data_batch_id=batch.id,
                strategy_version_id=strategy.id,
                data_version=batch.version,
                strategy_version=f"{strategy.code}:{strategy.version}",
                as_of_date=as_of_date,
                information_cutoff_at=information_cutoff_at,
                data_available=True,
                benchmark_symbol=str(payload.get("benchmark_symbol", "000300.SH")),
                initial_equity=initial_equity,
                max_investment_ratio=max_investment_ratio,
            )
            cost_model = _cost_model(cost)
            strategy_parameters = dict(strategy.parameters)
        result = DailyFlowApplicationService(cost_model).execute(
            request,
            bars=bars,
            strategy_parameters=strategy_parameters,
        )
        with self._session() as session:
            existing = session.scalar(
                select(models.BacktestRun).where(
                    models.BacktestRun.run_no == run_no,
                    models.BacktestRun.created_by == owner_id,
                )
            )
            if existing is not None:
                return self._daily_flow_record(existing)
            run = models.BacktestRun(
                id=uuid4(),
                run_no=run_no,
                data_batch_id=batch_id,
                strategy_version_id=strategy_id,
                cost_config_id=self._resolve_config_id(
                    session, models.CostConfigVersion, str(payload.get("cost_config_id", "cost_v1"))
                ),
                rule_config_id=self._resolve_config_id(
                    session, models.RuleConfigVersion, str(payload.get("rule_config_id", "rule_v1"))
                ),
                start_date=as_of_date,
                end_date=as_of_date,
                train_end=None,
                valid_end=None,
                oos_start=None,
                benchmark_symbol=request.benchmark_symbol,
                secondary_benchmark_symbol="000001.SH",
                universe_benchmark_enabled=True,
                execution_price_mode="NEXT_OPEN_ADJUSTED",
                initial_equity=initial_equity,
                config_snapshot=dict(payload),
                status="SUCCEEDED",
                result_usable=True,
                result_snapshot_hash=result.export_content_hash,
                result_summary={
                    "daily_flow": True,
                    "signal_symbols": list(result.signal_symbols),
                    "risk_rejected_symbols": list(result.risk_rejected_symbols),
                    "plan_count": len(result.plans),
                    "plan_execution_dates": [
                        value.isoformat() for value in result.plan_execution_dates
                    ],
                    "report": result.report,
                    "export_content_hash": result.export_content_hash,
                },
                created_by=owner_id,
                created_at=datetime.now(UTC),
            )
            session.add(run)
            session.flush()
            for stage_code in result.completed_stages:
                self._set_stage(session, run.id, stage_code, "SUCCEEDED")
            security_rows = session.scalars(
                select(models.Security).where(
                    models.Security.symbol.in_(result.historical_pool_symbols)
                )
            ).all()
            security_by_symbol = {security.symbol: security for security in security_rows}
            signal_by_symbol = {
                str(signal["symbol"]): signal for signal in result.report.get("signals", [])
            }
            for candidate in result.report.get("candidates", []):
                symbol = str(candidate["symbol"])
                security = security_by_symbol.get(symbol)
                if security is None:
                    continue
                signal = signal_by_symbol.get(symbol)
                session.add(
                    models.SignalSnapshot(
                        id=uuid4(),
                        strategy_version_id=strategy_id,
                        data_batch_id=batch_id,
                        as_of_date=as_of_date,
                        information_cutoff_at=information_cutoff_at,
                        security_id=security.id,
                        features=candidate,
                        eligibility={"selected": signal is not None},
                        score=Decimal(str(signal["score"])) if signal else Decimal("0"),
                        exclusion_reason=None if signal else "NOT_SELECTED",
                    )
                )
            for index, plan in enumerate(result.plans, start=1):
                security = security_by_symbol.get(plan.symbol)
                if security is None:
                    continue
                session.add(
                    models.OrderPlan(
                        id=uuid4(),
                        plan_no=f"{run_no}-p{index}",
                        run_id=run.id,
                        as_of_date=plan.signal_date,
                        execution_date=plan.execution_date,
                        security_id=security.id,
                        side=plan.side,
                        planned_quantity=plan.quantity,
                        reference_low=plan.reference_low,
                        reference_high=plan.reference_high,
                        estimated_cost=plan.estimated_cost,
                        trigger_reasons={"score": str(plan.score), "rank": plan.rank},
                        risk_snapshot={"risk_state": plan.risk_state},
                        status="PENDING_CONFIRMATION",
                        idempotency_key=f"{run_no}:{plan.symbol}:{plan.execution_date.isoformat()}",
                    )
                )
            session.add(
                models.PortfolioSnapshot(
                    id=uuid4(),
                    run_id=run.id,
                    account_id=owner_id,
                    trade_date=as_of_date,
                    cash=initial_equity,
                    market_value=Decimal("0"),
                    equity=initial_equity,
                    high_watermark=initial_equity,
                    drawdown=Decimal("0"),
                    risk_state="NORMAL",
                )
            )
            session.add(
                models.LedgerEntry(
                    id=uuid4(),
                    account_id=owner_id,
                    run_id=run.id,
                    trade_date=as_of_date,
                    entry_type="OPENING_BALANCE",
                    security_id=None,
                    quantity_delta=0,
                    cash_delta=initial_equity,
                    fee_delta=Decimal("0"),
                    source_id=run.id,
                    created_at=datetime.now(UTC),
                )
            )
            session.add(
                models.DailyReport(
                    id=uuid4(),
                    report_date=as_of_date,
                    run_id=run.id,
                    status="SUCCEEDED",
                    market_switch=True,
                    content=result.report,
                    result_usable=True,
                    content_hash=result.export_content_hash,
                )
            )
            session.add(
                models.ReportArtifact(
                    id=uuid4(),
                    run_id=run.id,
                    report_type="DAILY",
                    file_path=f"reports/{run_no}.json",
                    content_hash=result.export_content_hash,
                    created_at=datetime.now(UTC),
                )
            )
            return self._daily_flow_record(run)

    def execute_run(self, run_id: str, owner_id: UUID) -> dict[str, Any] | None:
        """Replay a queued run from persisted bars and persist its accounting result."""
        with self._session() as session:
            run = session.scalar(
                select(models.BacktestRun).where(
                    models.BacktestRun.run_no == run_id,
                    models.BacktestRun.created_by == owner_id,
                )
            )
            if run is None:
                return None
            if run.status == "SUCCEEDED":
                return self._run_record(run)
            if run.status == "RUNNING":
                raise StateConflictError("backtest run is already executing")
            snapshot = dict(run.config_snapshot or {})
            try:
                batch_ids = _ordered_batch_ids(
                    {
                        "data_batch_id": str(run.data_batch_id),
                        "data_batch_ids": snapshot.get("data_batch_ids"),
                    }
                )
            except ValueError:
                batch_ids = ()
            batches_by_id = {
                batch.id: batch
                for batch in session.scalars(
                    select(models.DataBatch).where(models.DataBatch.id.in_(batch_ids))
                ).all()
            }
            batches = tuple(batches_by_id.get(batch_id) for batch_id in batch_ids)
            strategy = session.get(models.StrategyVersion, run.strategy_version_id)
            cost = session.get(models.CostConfigVersion, run.cost_config_id)
            rule = session.get(models.RuleConfigVersion, run.rule_config_id)
            if (
                not batches
                or any(batch is None for batch in batches)
                or any(
                    batch is not None
                    and (
                        batch.status not in {"AVAILABLE", "WARNING_AVAILABLE"}
                        or batch.owner_id != str(owner_id)
                    )
                    for batch in batches
                )
                or strategy is None
                or strategy.status != "PUBLISHED"
                or cost is None
                or rule is None
                or rule.status != "PUBLISHED"
            ):
                run.status = "UNAVAILABLE"
                run.result_usable = False
                run.result_summary = {
                    "available": False,
                    "reason": "backtest prerequisites are unavailable",
                }
                self._set_stage(
                    session,
                    run.id,
                    "data_quality",
                    "UNAVAILABLE",
                    "backtest prerequisites are unavailable",
                )
                return self._run_record(run)
            typed_batches = tuple(batch for batch in batches if batch is not None)
            batch_set_error = self._batch_set_error(
                session,
                typed_batches,
                start_date=run.start_date,
                end_date=run.end_date,
            )
            current_data_snapshot = self._backtest_data_snapshot(
                session, typed_batches, end_date=run.end_date
            )
            strategy_type = str(snapshot.get("strategy_type") or "")
            implementation_version = str(snapshot.get("strategy_implementation_version") or "")
            rule_version = str(snapshot.get("rule_version") or "")
            rule_config = snapshot.get("rule_config")
            if not isinstance(rule_config, dict):
                rule_config = {}
            try:
                definition = get_strategy_definition(strategy_type)
            except ValueError:
                definition = None
            current_cost_snapshot = _cost_config_snapshot(cost)
            version_mismatch = (
                definition is None
                or batch_set_error is not None
                or definition.implementation_version != implementation_version
                or rule.version != rule_version
                or dict(rule.config) != rule_config
                or _hash_payload(rule_config) != snapshot.get("rule_config_hash")
                or current_cost_snapshot != snapshot.get("cost_config")
                or _hash_payload(current_cost_snapshot) != snapshot.get("cost_config_hash")
                or current_data_snapshot
                != {
                    key: snapshot.get(key)
                    for key in (
                        "data_batches",
                        "data_batch_set_hash",
                        "status_history_hash",
                        "industry_history_hash",
                        "security_master_hash",
                        "trading_calendar_hash",
                    )
                }
            )
            if version_mismatch:
                run.status = "UNAVAILABLE"
                run.result_usable = False
                run.result_summary = {
                    "available": False,
                    "reason": (
                        "snapshotted implementation is unavailable because strategy, rule, "
                        "cost, or data inputs no longer match"
                    ),
                }
                self._set_stage(
                    session,
                    run.id,
                    "data_quality",
                    "UNAVAILABLE",
                    str(run.result_summary["reason"]),
                )
                return self._run_record(run)
            rows = session.execute(
                select(models.DailyBar, models.Security)
                .join(models.Security, models.DailyBar.security_id == models.Security.id)
                .where(
                    models.DailyBar.data_batch_id.in_(batch_ids),
                    models.DailyBar.trade_date <= run.end_date,
                )
                .order_by(models.DailyBar.trade_date, models.Security.symbol)
            ).all()
            cutoff = self._timestamp(snapshot.get("information_cutoff_at"))
            maximum_cutoff = max(typed_batches, key=lambda item: as_utc(item.information_cutoff_at))
            cutoff = cutoff or maximum_cutoff.information_cutoff_at
            if as_utc(cutoff) > as_utc(maximum_cutoff.information_cutoff_at) or any(
                as_utc(batch.available_at) > as_utc(cutoff) for batch in typed_batches
            ):
                cutoff_invalid = True
            else:
                cutoff_invalid = False
            available_rows = [
                (bar, security)
                for bar, security in rows
                if bar.available_at is not None and as_utc(bar.available_at) <= as_utc(cutoff)
            ]
            calendar_dates = tuple(
                session.scalars(
                    select(models.TradeCalendar.trade_date)
                    .where(
                        models.TradeCalendar.is_open.is_(True),
                        models.TradeCalendar.trade_date <= run.end_date,
                    )
                    .distinct()
                    .order_by(models.TradeCalendar.trade_date)
                ).all()
            )
            run_trade_dates = tuple(
                trade_date
                for trade_date in calendar_dates
                if run.start_date <= trade_date <= run.end_date
            )
            requested_rows = [
                (bar, security)
                for bar, security in available_rows
                if run.start_date <= bar.trade_date <= run.end_date
            ]
            if cutoff_invalid or not requested_rows or not run_trade_dates:
                run.status = "UNAVAILABLE"
                run.result_usable = False
                run.result_summary = {
                    "available": False,
                    "reason": (
                        "information cutoff is later than the data batch cutoff"
                        if cutoff_invalid
                        else (
                            "authoritative trading calendar is unavailable"
                            if not run_trade_dates
                            else "backtest data contains no bars available before the information cutoff"
                        )
                    ),
                }
                self._set_stage(
                    session,
                    run.id,
                    "data_quality",
                    "UNAVAILABLE",
                    str(run.result_summary["reason"]),
                )
                return self._run_record(run)
            benchmark_dates = {
                bar.trade_date
                for bar, security in requested_rows
                if security.symbol == run.benchmark_symbol and bar.adjusted_close is not None
            }
            missing_benchmark_dates = [
                trade_date for trade_date in run_trade_dates if trade_date not in benchmark_dates
            ]
            if missing_benchmark_dates:
                run.status = "UNAVAILABLE"
                run.result_usable = False
                run.result_summary = {
                    "available": False,
                    "reason": (
                        "benchmark market snapshot is unavailable on "
                        f"{missing_benchmark_dates[0].isoformat()}"
                    ),
                }
                self._set_stage(
                    session,
                    run.id,
                    "data_quality",
                    "UNAVAILABLE",
                    str(run.result_summary["reason"]),
                )
                return self._run_record(run)
            status_rows = session.scalars(
                select(models.SecurityStatusHistory).where(
                    models.SecurityStatusHistory.source_batch_id.in_(batch_ids)
                )
            ).all()
            batch_priority = {batch_id: ordinal for ordinal, batch_id in enumerate(batch_ids)}
            statuses_by_security: dict[UUID, list[models.SecurityStatusHistory]] = {}
            for status in status_rows:
                statuses_by_security.setdefault(status.security_id, []).append(status)
            memberships = session.scalars(
                select(models.IndustryMembershipHistory).where(
                    models.IndustryMembershipHistory.source_batch_id.in_(batch_ids)
                )
            ).all()
            memberships_by_security: dict[UUID, list[models.IndustryMembershipHistory]] = {}
            for membership in memberships:
                memberships_by_security.setdefault(membership.security_id, []).append(membership)

            def status_as_of(
                security_id: UUID, trade_date: date
            ) -> models.SecurityStatusHistory | None:
                return self._status_as_of(
                    statuses_by_security.get(security_id, ()),
                    trade_date,
                    min(
                        as_utc(cutoff),
                        datetime.combine(trade_date, time.max, _SHANGHAI).astimezone(UTC),
                    ),
                    batch_priority,
                )

            def industry_as_of(security_id: UUID, trade_date: date) -> str | None:
                membership, _ = self._industry_as_of(
                    memberships_by_security.get(security_id, ()),
                    trade_date,
                    min(
                        as_utc(cutoff),
                        datetime.combine(trade_date, time.max, _SHANGHAI).astimezone(UTC),
                    ),
                    batch_priority,
                )
                return membership.industry_code if membership is not None else None

            amount_history: dict[UUID, list[Decimal]] = {}
            universe_eligibility: dict[tuple[UUID, date], bool] = {}
            securities_by_id: dict[UUID, models.Security] = {}
            for bar, security in available_rows:
                securities_by_id[security.id] = security
                history = amount_history.setdefault(security.id, [])
                history.append(bar.amount)
                if len(history) > 20:
                    del history[0]
                current_status = status_as_of(security.id, bar.trade_date)
                listing_days = bisect_right(calendar_dates, bar.trade_date) - bisect_left(
                    calendar_dates, security.list_date
                )
                universe_eligibility[(security.id, bar.trade_date)] = bool(
                    security.security_type == "COMMON"
                    and current_status is not None
                    and current_status.board == "MAIN"
                    and not current_status.is_st
                    and not current_status.is_suspended
                    and not current_status.is_delist_period
                    and (security.delist_date is None or security.delist_date > bar.trade_date)
                    and listing_days >= 60
                    and len(history) >= 20
                    and median(history) >= Decimal("20000000")
                )
            suspended_by_date = {
                trade_date: frozenset(
                    security.symbol
                    for security in securities_by_id.values()
                    if (suspension_status := status_as_of(security.id, trade_date)) is not None
                    and suspension_status.is_suspended
                )
                for trade_date in run_trade_dates
            }

            def build_market_bar(bar: models.DailyBar, security: models.Security) -> MarketBar:
                current_status = status_as_of(security.id, bar.trade_date)
                limit_up, limit_down, limit_status_known = self._limit_state(
                    bar, current_status, None
                )
                return MarketBar(
                    symbol=security.symbol,
                    trade_date=bar.trade_date,
                    open=bar.adjusted_open,
                    low=bar.adjusted_low,
                    high=bar.adjusted_high,
                    close=bar.adjusted_close,
                    is_suspended=bool(current_status and current_status.is_suspended),
                    limit_up=limit_up,
                    limit_down=limit_down,
                    limit_status_known=limit_status_known,
                    close_limit_up=bar.close_limit_up,
                    close_limit_down=bar.close_limit_down,
                    amount=bar.amount,
                    available_at=bar.available_at,
                    is_st=bool(current_status and current_status.is_st),
                    is_delisted=bool(
                        (current_status and current_status.is_delist_period)
                        or (
                            security.delist_date is not None
                            and bar.trade_date >= security.delist_date
                        )
                    ),
                    industry=industry_as_of(security.id, bar.trade_date),
                    list_date=security.list_date,
                    security_type=security.security_type,
                    board=current_status.board if current_status is not None else None,
                    universe_eligible=universe_eligibility.get(
                        (security.id, bar.trade_date), False
                    ),
                )

            bars = tuple(
                build_market_bar(bar, security)
                for bar, security in available_rows
                if security.list_date <= bar.trade_date
            )
            model = _cost_model(cost)
            config = BacktestConfig(
                start=run.start_date,
                end=run.end_date,
                initial_equity=run.initial_equity,
                benchmark=run.benchmark_symbol,
                training_end=run.train_end,
                validation_end=run.valid_end,
                execution_mode=run.execution_price_mode,
                fill_mode="FULL_OR_NONE",
                oos_start=run.oos_start,
            )
            data_version = str(current_data_snapshot["data_batch_set_hash"])
            strategy_version = f"{strategy.code}:{strategy.version}"
            strategy_parameters_value = snapshot.get("effective_parameters")
            if not isinstance(strategy_parameters_value, dict):
                raise StateConflictError("snapshotted strategy parameters are unavailable")
            strategy_parameters = dict(strategy_parameters_value)
            secondary_benchmark = run.secondary_benchmark_symbol
            universe_benchmark_enabled = run.universe_benchmark_enabled
            batch_snapshot_value = current_data_snapshot["data_batches"]
            if not isinstance(batch_snapshot_value, list):
                raise StateConflictError("snapshotted data batches are unavailable")
            runner_data_batches = tuple(
                dict(item) for item in batch_snapshot_value if isinstance(item, dict)
            )
            run.status = "RUNNING"
            self._set_stage(session, run.id, "data_quality", "SUCCEEDED")
            self._set_stage(session, run.id, "replay", "RUNNING")
        try:
            result = StatefulBacktestRunner(model).run(
                config,
                bars,
                data_version=data_version,
                strategy_version=strategy_version,
                strategy_type=strategy_type,
                strategy_parameters=strategy_parameters,
                information_cutoff_at=cutoff,
                strategy_implementation_version=implementation_version,
                trading_dates=run_trade_dates,
                rule_version=rule_version,
                rule_config=rule_config,
                secondary_benchmark=secondary_benchmark,
                universe_benchmark_enabled=universe_benchmark_enabled,
                suspended_symbols_by_date=suspended_by_date,
                data_batches=runner_data_batches,
                cost_config=current_cost_snapshot,
                cost_config_hash=str(snapshot["cost_config_hash"]),
            )
        except Exception as exc:
            with self._session() as session:
                run = session.scalar(
                    select(models.BacktestRun).where(
                        models.BacktestRun.run_no == run_id,
                        models.BacktestRun.created_by == owner_id,
                    )
                )
                if run is None:
                    return None
                unavailable = isinstance(exc, BenchmarkUnavailableError)
                run.status = "UNAVAILABLE" if unavailable else "FAILED"
                run.result_usable = False
                run.result_summary = {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
                self._set_stage(
                    session,
                    run.id,
                    "replay",
                    "UNAVAILABLE" if unavailable else "FAILED",
                    str(exc),
                )
                return self._run_record(run)
        with self._session() as session:
            run = session.scalar(
                select(models.BacktestRun).where(
                    models.BacktestRun.run_no == run_id,
                    models.BacktestRun.created_by == owner_id,
                )
            )
            if run is None:
                return None
            run.status = "SUCCEEDED"
            run.result_usable = result.metrics.available
            run.result_snapshot_hash = result.snapshot_hash
            run.result_summary = self._result_summary(result)
            artifact_path = f"reports/{run.run_no}.json"
            run.result_summary["export"] = {
                "report_type": "BACKTEST",
                "file_path": artifact_path,
                "content_hash": result.snapshot_hash,
            }
            self._set_stage(session, run.id, "replay", "SUCCEEDED")
            self._set_stage(session, run.id, "metrics", "SUCCEEDED")
            run_trade_dates = tuple(
                sorted(
                    {
                        bar.trade_date
                        for bar in bars
                        if run.start_date <= bar.trade_date <= run.end_date
                    }
                )
            )
            self._persist_snapshots(
                session,
                run,
                result,
                run_trade_dates,
                warning_threshold=Decimal(str(rule_config["drawdown_stop_new"])),
                review_threshold=Decimal(str(rule_config["drawdown_review_required"])),
            )
            self._persist_series(session, run, result)
            self._persist_metrics(session, run, result)
            session.add(
                models.ReportArtifact(
                    id=uuid4(),
                    run_id=run.id,
                    report_type="BACKTEST",
                    file_path=artifact_path,
                    content_hash=result.snapshot_hash,
                    created_at=datetime.now(UTC),
                )
            )
            return self._run_record(run)

    def get_run(self, run_id: str, owner_id: UUID) -> dict[str, Any] | None:
        with self._session() as session:
            run = session.scalar(
                select(models.BacktestRun).where(
                    models.BacktestRun.run_no == run_id,
                    models.BacktestRun.created_by == owner_id,
                )
            )
            if run is None:
                return None
            record = self._run_record(run)
            stages = session.scalars(
                select(models.RunStage)
                .where(models.RunStage.run_id == run.id)
                .order_by(models.RunStage.stage_code)
            ).all()
            record["stages"] = [
                {
                    "stage": stage.stage_code,
                    "status": stage.status,
                    "error": stage.error_summary,
                }
                for stage in stages
            ]
            return record

    def list_backtests(
        self, owner_id: UUID, status: str | None, page: int, page_size: int
    ) -> dict[str, Any]:
        with self._session() as session:
            query = select(models.BacktestRun).where(models.BacktestRun.created_by == owner_id)
            if status is not None:
                query = query.where(models.BacktestRun.status == status)
            runs = session.scalars(
                query.order_by(models.BacktestRun.created_at.desc(), models.BacktestRun.id.desc())
            ).all()
            return self._page([self._run_record(run) for run in runs], page, page_size)

    def get_run_child(self, run_id: str, owner_id: UUID, kind: str) -> dict[str, Any] | None:
        record = self.get_run(run_id, owner_id)
        if record is None:
            return None
        if kind == "trades":
            summary = record.get("result_summary", {})
            trades = summary.get("trades", [])
            return {
                "items": trades if isinstance(trades, list) else [],
                "page": 1,
                "page_size": 50,
                "total": len(trades) if isinstance(trades, list) else 0,
                "skipped_orders": summary.get("skipped_orders", []),
            }
        with self._session() as session:
            run = session.scalar(
                select(models.BacktestRun).where(models.BacktestRun.run_no == run_id)
            )
            metrics = dict(record.get("result_summary", {}).get("metrics", {}))
            persisted_segment_metrics: dict[str, dict[str, str]] = {}
            if run is not None:
                for metric in session.scalars(
                    select(models.PerformanceMetric).where(
                        models.PerformanceMetric.run_id == run.id
                    )
                ).all():
                    key = f"{metric.series_code}:{metric.segment}"
                    persisted_segment_metrics.setdefault(key, {})[metric.metric_code] = str(
                        metric.metric_value
                    )
                    if metric.series_code == "STRATEGY" and metric.segment == "FULL":
                        metrics[metric.metric_code] = str(metric.metric_value)
        summary = record.get("result_summary", {})
        return {
            "run_id": run_id,
            "result_usable": record["result_usable"],
            "status": record["status"],
            "snapshot_hash": record.get("snapshot_hash"),
            "metrics": metrics,
            "segment_metrics": persisted_segment_metrics or summary.get("segment_metrics", {}),
            "series": summary.get("series", {}),
            "strategy_type": record.get("strategy_type"),
            "effective_parameters": record.get("effective_parameters", {}),
            "strategy_implementation_version": record.get("strategy_implementation_version"),
            "engine_version": record.get("engine_version"),
            "risk_state": summary.get("risk_state"),
            "excess_returns": summary.get("excess_returns", {}),
            "export": summary.get("export"),
            "unavailable_reasons": (
                []
                if record["result_usable"]
                else [record.get("result_summary", {}).get("reason", "run has not completed")]
            ),
        }

    def list_pool(
        self,
        trade_date: date,
        status: str | None,
        page: int,
        page_size: int,
        owner_id: UUID | None = None,
    ) -> dict[str, Any]:
        with self._session() as session:
            query = (
                select(models.Security, models.DailyBar, models.DataBatch)
                .join(models.DailyBar, models.DailyBar.security_id == models.Security.id)
                .join(models.DataBatch, models.DataBatch.id == models.DailyBar.data_batch_id)
                .where(models.DailyBar.trade_date == trade_date)
                .order_by(models.Security.symbol)
            )
            query = query.where(models.DataBatch.status.in_(("AVAILABLE", "WARNING_AVAILABLE")))
            if owner_id is not None:
                query = query.where(models.DataBatch.owner_id == str(owner_id))
            records: dict[str, dict[str, Any]] = {}
            for security, _, batch in session.execute(query).all():
                if security.symbol in records:
                    continue
                status_row = session.scalars(
                    select(models.SecurityStatusHistory)
                    .where(
                        models.SecurityStatusHistory.security_id == security.id,
                        models.SecurityStatusHistory.effective_date <= trade_date,
                        models.SecurityStatusHistory.source_batch_id == batch.id,
                        models.SecurityStatusHistory.observed_at <= batch.information_cutoff_at,
                    )
                    .order_by(
                        models.SecurityStatusHistory.effective_date.desc(),
                        models.SecurityStatusHistory.observed_at.desc(),
                    )
                ).first()
                is_excluded = bool(
                    status_row
                    and (status_row.is_st or status_row.is_suspended or status_row.is_delist_period)
                )
                reasons = []
                if status_row and status_row.is_st:
                    reasons.append("st")
                if status_row and status_row.is_suspended:
                    reasons.append("suspended")
                if status_row and status_row.is_delist_period:
                    reasons.append("delist_period")
                if security.list_date > trade_date:
                    is_excluded = True
                    reasons.append("not_listed")
                if security.delist_date is not None and security.delist_date <= trade_date:
                    is_excluded = True
                    reasons.append("delisted")
                records[security.symbol] = {
                    "symbol": security.symbol,
                    "exchange": security.exchange,
                    "board": status_row.board if status_row else "MAIN",
                    "in_pool": not is_excluded,
                    "exclusion_reasons": reasons,
                    "status_as_of": (
                        "ST"
                        if status_row and status_row.is_st
                        else "SUSPENDED"
                        if status_row and status_row.is_suspended
                        else "NORMAL"
                    ),
                    "source_batch_id": str(batch.id),
                    "trade_date": trade_date.isoformat(),
                }
            rows = list(records.values())
            if status == "EXCLUDED":
                rows = [row for row in rows if not row["in_pool"]]
            elif status == "ELIGIBLE":
                rows = [row for row in rows if row["in_pool"]]
            return self._page(rows, page, page_size)

    def list_plans(
        self, execution_date: date, page: int, page_size: int, owner_id: UUID | None = None
    ) -> dict[str, Any]:
        with self._session() as session:
            query = select(models.OrderPlan).where(
                models.OrderPlan.execution_date == execution_date
            )
            if owner_id is not None:
                query = query.join(
                    models.BacktestRun, models.OrderPlan.run_id == models.BacktestRun.id
                )
                query = query.where(models.BacktestRun.created_by == owner_id)
            plans = session.scalars(query.order_by(models.OrderPlan.plan_no)).all()
            return self._page([self._plan_record(plan) for plan in plans], page, page_size)

    def daily_report(self, report_date: date, owner_id: UUID | None = None) -> dict[str, Any]:
        with self._session() as session:
            query = select(models.DailyReport).where(models.DailyReport.report_date == report_date)
            if owner_id is not None:
                query = query.join(
                    models.BacktestRun, models.DailyReport.run_id == models.BacktestRun.id
                )
                query = query.where(models.BacktestRun.created_by == owner_id)
            report = session.scalar(query)
            if report is None:
                return {
                    "report_date": report_date.isoformat(),
                    "run_id": None,
                    "status": "UNAVAILABLE",
                    "result_usable": False,
                    "data_quality": "UNAVAILABLE",
                    "unavailable_reasons": ["daily report has not been generated"],
                    "notice": "研究用途；不构成投资建议；不承诺收益；不自动下单。",
                }
            return {
                **report.content,
                "report_date": report.report_date.isoformat(),
                "run_id": str(report.run_id),
                "status": report.status,
                "result_usable": report.result_usable,
            }

    def snapshots(self, account_id: str, page: int, page_size: int) -> dict[str, Any]:
        identifier = _uuid(account_id)
        if identifier is None:
            return self._page([], page, page_size)
        with self._session() as session:
            snapshots = session.scalars(
                select(models.PortfolioSnapshot)
                .where(models.PortfolioSnapshot.account_id == identifier)
                .order_by(models.PortfolioSnapshot.trade_date)
            ).all()
            rows = [
                {
                    "account_id": account_id,
                    "snapshot_date": item.trade_date.isoformat(),
                    "equity": str(item.equity),
                    "cash": str(item.cash),
                    "holdings_value": str(item.market_value),
                    "risk_state": item.risk_state,
                }
                for item in snapshots
            ]
            return self._page(rows, page, page_size)

    def create_execution(self, payload: dict[str, Any], owner_id: UUID) -> dict[str, Any] | None:
        plan_id = _uuid(str(payload["plan_id"]))
        if plan_id is None:
            return None
        with self._session() as session:
            plan = session.scalar(
                select(models.OrderPlan)
                .join(models.BacktestRun, models.OrderPlan.run_id == models.BacktestRun.id)
                .where(
                    models.OrderPlan.id == plan_id,
                    models.BacktestRun.created_by == owner_id,
                )
            )
            if plan is None:
                return None
            idempotency_key = str(payload.get("idempotency_key") or "") or None
            if idempotency_key is not None:
                existing = session.scalar(
                    select(models.ExecutionRecord)
                    .where(
                        models.ExecutionRecord.plan_id == plan.id,
                        models.ExecutionRecord.idempotency_key == idempotency_key,
                    )
                    .limit(1)
                )
                if existing is not None:
                    replay_snapshot = session.scalar(
                        select(models.PortfolioSnapshot).where(
                            models.PortfolioSnapshot.run_id == plan.run_id,
                            models.PortfolioSnapshot.account_id == owner_id,
                            models.PortfolioSnapshot.trade_date == existing.executed_at.date(),
                        )
                    )
                    fees = sum(
                        (
                            existing.commission,
                            existing.stamp_tax,
                            existing.transfer_fee,
                            existing.other_fee,
                        ),
                        Decimal("0"),
                    )
                    return {
                        "execution_id": str(existing.id),
                        "execution_no": existing.execution_no,
                        "status": "FILLED"
                        if existing.unfilled_quantity == 0
                        else "PARTIALLY_FILLED",
                        "plan_id": str(plan.id),
                        "quantity": existing.quantity,
                        "unfilled_quantity": existing.unfilled_quantity,
                        "price": str(existing.price),
                        "cash": str(replay_snapshot.cash) if replay_snapshot is not None else None,
                        "fee_total": str(fees),
                        "replayed": True,
                    }
            if plan.status not in {"CONFIRMED", "PARTIALLY_FILLED"}:
                from app.core.errors import StateConflictError

                raise StateConflictError("plan must be confirmed before recording execution")
            quantity = int(payload["quantity"])
            unfilled = int(payload["unfilled_quantity"])
            prior_filled = int(
                session.scalar(
                    select(func.coalesce(func.sum(models.ExecutionRecord.quantity), 0)).where(
                        models.ExecutionRecord.plan_id == plan.id
                    )
                )
                or 0
            )
            remaining_quantity = plan.planned_quantity - prior_filled
            if quantity + unfilled != remaining_quantity:
                from app.core.errors import RuleViolationError

                raise RuleViolationError(
                    "filled and unfilled quantities must equal the remaining planned quantity"
                )
            status = "FILLED" if unfilled == 0 else "PARTIALLY_FILLED"
            price = Decimal(str(payload["price"]))
            fees = sum(
                (
                    Decimal(str(payload[name]))
                    for name in ("commission", "stamp_tax", "transfer_fee", "other_fee")
                ),
                Decimal("0"),
            )
            if price <= 0 or fees < 0:
                from app.core.errors import RuleViolationError

                raise RuleViolationError("manual execution price and fees must be valid")
            latest_snapshot = session.scalar(
                select(models.PortfolioSnapshot)
                .where(models.PortfolioSnapshot.run_id == plan.run_id)
                .order_by(models.PortfolioSnapshot.trade_date.desc())
            )
            if latest_snapshot is None:
                from app.core.errors import StateConflictError

                raise StateConflictError("opening ledger snapshot is missing")
            position_rows = session.scalars(
                select(models.PositionSnapshot).where(
                    models.PositionSnapshot.portfolio_snapshot_id == latest_snapshot.id
                )
            ).all()
            positions = {position.security_id: position for position in position_rows}
            current = positions.get(plan.security_id)
            next_cash = Decimal(str(latest_snapshot.cash))
            filled_notional = price * Decimal(quantity)
            if plan.side == "BUY":
                total_debit = filled_notional + fees
                if total_debit > next_cash:
                    from app.core.errors import RuleViolationError

                    raise RuleViolationError("manual execution would make cash negative")
                next_cash -= total_debit
                old_quantity = current.quantity if current is not None else 0
                old_cost = (
                    current.avg_cost * Decimal(old_quantity)
                    if current is not None
                    else Decimal("0")
                )
                next_quantity = old_quantity + quantity
                next_avg_cost = (old_cost + total_debit) / Decimal(next_quantity)
                next_available = 0
            elif plan.side == "SELL":
                if current is None or quantity > current.available_quantity:
                    from app.core.errors import RuleViolationError

                    raise RuleViolationError("manual execution would make holdings negative")
                next_cash += filled_notional - fees
                next_quantity = current.quantity - quantity
                next_avg_cost = current.avg_cost
                next_available = current.available_quantity - quantity
            else:
                from app.core.errors import RuleViolationError

                raise RuleViolationError("unsupported plan side")
            execution = models.ExecutionRecord(
                id=uuid4(),
                execution_no=f"exec_{uuid4().hex[:12]}",
                plan_id=plan.id,
                idempotency_key=idempotency_key,
                execution_type=str(payload["execution_type"]),
                executed_at=datetime.fromisoformat(str(payload["executed_at"])),
                quantity=quantity,
                price=price,
                commission=payload["commission"],
                stamp_tax=payload["stamp_tax"],
                transfer_fee=payload["transfer_fee"],
                other_fee=payload["other_fee"],
                unfilled_quantity=unfilled,
                unfilled_reason=payload.get("note") if unfilled else None,
                source="MANUAL",
            )
            session.add(execution)
            plan.status = status
            plan.version += 1
            session.flush()
            session.add(
                models.LedgerEntry(
                    id=uuid4(),
                    account_id=owner_id,
                    run_id=plan.run_id,
                    trade_date=execution.executed_at.date(),
                    entry_type="MANUAL_EXECUTION",
                    security_id=plan.security_id,
                    quantity_delta=quantity if plan.side == "BUY" else -quantity,
                    cash_delta=(
                        -filled_notional - fees if plan.side == "BUY" else filled_notional - fees
                    ),
                    fee_delta=fees,
                    source_id=execution.id,
                    created_at=datetime.now(UTC),
                )
            )
            execution_snapshot = session.scalar(
                select(models.PortfolioSnapshot).where(
                    models.PortfolioSnapshot.run_id == plan.run_id,
                    models.PortfolioSnapshot.account_id == owner_id,
                    models.PortfolioSnapshot.trade_date == execution.executed_at.date(),
                )
            )
            if execution_snapshot is None:
                market_value = sum(
                    (
                        position.market_price * Decimal(position.quantity)
                        for position in positions.values()
                        if position.security_id != plan.security_id
                    ),
                    Decimal("0"),
                ) + price * Decimal(next_quantity)
                equity = next_cash + market_value
                high_watermark = max(Decimal(str(latest_snapshot.high_watermark)), equity)
                drawdown = (
                    (high_watermark - equity) / high_watermark if high_watermark else Decimal("0")
                )
                execution_snapshot = models.PortfolioSnapshot(
                    id=uuid4(),
                    run_id=plan.run_id,
                    account_id=owner_id,
                    trade_date=execution.executed_at.date(),
                    cash=next_cash,
                    market_value=market_value,
                    equity=equity,
                    high_watermark=high_watermark,
                    drawdown=drawdown,
                    risk_state="NORMAL",
                )
                session.add(execution_snapshot)
                session.flush()
                if next_quantity > 0:
                    session.add(
                        models.PositionSnapshot(
                            id=uuid4(),
                            portfolio_snapshot_id=execution_snapshot.id,
                            security_id=plan.security_id,
                            quantity=next_quantity,
                            available_quantity=next_available,
                            avg_cost=next_avg_cost,
                            market_price=price,
                            industry_code=None,
                        )
                    )
            else:
                # Multiple manual fills can land on the same trade date.  The
                # ledger entry above is append-only, but the date snapshot is
                # unique, so update its cash/equity and position in place.
                market_value = sum(
                    (
                        position.market_price * Decimal(position.quantity)
                        for position in positions.values()
                        if position.security_id != plan.security_id
                    ),
                    Decimal("0"),
                ) + price * Decimal(next_quantity)
                equity = next_cash + market_value
                high_watermark = max(Decimal(str(latest_snapshot.high_watermark)), equity)
                execution_snapshot.cash = next_cash
                execution_snapshot.market_value = market_value
                execution_snapshot.equity = equity
                execution_snapshot.high_watermark = high_watermark
                execution_snapshot.drawdown = (
                    (high_watermark - equity) / high_watermark if high_watermark else Decimal("0")
                )
                if next_quantity > 0:
                    if current is None:
                        current = models.PositionSnapshot(
                            id=uuid4(),
                            portfolio_snapshot_id=execution_snapshot.id,
                            security_id=plan.security_id,
                            quantity=next_quantity,
                            available_quantity=next_available,
                            avg_cost=next_avg_cost,
                            market_price=price,
                            industry_code=None,
                        )
                        session.add(current)
                    else:
                        current.quantity = next_quantity
                        current.available_quantity = next_available
                        current.avg_cost = next_avg_cost
                        current.market_price = price
                elif current is not None:
                    session.delete(current)
            return {
                "execution_id": str(execution.id),
                "execution_no": execution.execution_no,
                "status": status,
                "plan_id": str(plan.id),
                "quantity": quantity,
                "unfilled_quantity": unfilled,
                "price": str(execution.price),
                "cash": str(next_cash),
                "fee_total": str(fees),
                "replayed": False,
            }

    def confirm_plan(
        self, plan_id: str, owner_id: UUID, decision: str, expected_version: int, note: str
    ) -> dict[str, Any] | None:
        identifier = _uuid(plan_id)
        if identifier is None:
            return None
        with self._session() as session:
            plan = session.scalar(
                select(models.OrderPlan)
                .join(models.BacktestRun, models.OrderPlan.run_id == models.BacktestRun.id)
                .where(
                    models.OrderPlan.id == identifier,
                    models.BacktestRun.created_by == owner_id,
                )
            )
            if plan is None:
                return None
            if expected_version != plan.version or plan.status != "PENDING_CONFIRMATION":
                raise StateConflictError("plan version or state does not match")
            expires_at = datetime.combine(plan.execution_date, time(1, 25), tzinfo=UTC)
            if decision == "CONFIRM" and datetime.now(UTC) >= expires_at:
                raise StateConflictError("plan has expired and must be regenerated")
            if decision == "CONFIRM" and plan.risk_snapshot.get("risk_state") in {
                "STOP_NEW",
                "MANUAL_REVIEW",
            }:
                from app.core.errors import RuleViolationError

                raise RuleViolationError("risk state does not allow new positions")
            plan.status = "CONFIRMED" if decision == "CONFIRM" else "SKIPPED"
            plan.version += 1
            return self._plan_record(plan) | {"review_note": note}

    def create_export(self, report_id: str, owner_id: UUID) -> dict[str, Any]:
        identifier = _uuid(report_id)
        with self._session() as session:
            artifact_filters = [models.BacktestRun.created_by == owner_id]
            if identifier is not None:
                artifact_filters.append(models.ReportArtifact.run_id == identifier)
            else:
                artifact_filters.append(models.BacktestRun.run_no == report_id)
            artifact = session.scalar(
                select(models.ReportArtifact)
                .join(models.BacktestRun, models.ReportArtifact.run_id == models.BacktestRun.id)
                .where(*artifact_filters)
            )
            if artifact is None:
                return {
                    "export_id": None,
                    "report_id": report_id,
                    "status": "UNAVAILABLE",
                    "unavailable_reasons": ["report artifact is not available"],
                }
            self._ensure_user(session, owner_id)
            export = models.ReportExport(
                id=uuid4(),
                report_id=report_id,
                owner_id=owner_id,
                status="READY",
                file_path=artifact.file_path,
                content_hash=artifact.content_hash,
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=15),
            )
            session.add(export)
            session.flush()
            return {
                "export_id": str(export.id),
                "report_id": report_id,
                "status": export.status,
                "file_path": export.file_path,
                "content_hash": export.content_hash,
                "expires_at": export.expires_at.isoformat(),
            }

    def get_export(self, export_id: str, owner_id: UUID | None = None) -> dict[str, Any] | None:
        identifier = _uuid(export_id)
        if identifier is None:
            return None
        with self._session() as session:
            query = select(models.ReportExport).where(models.ReportExport.id == identifier)
            if owner_id is not None:
                query = query.where(models.ReportExport.owner_id == owner_id)
            export = session.scalar(query)
            if export is None:
                return None
            expires_at = export.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            status = "EXPIRED" if expires_at <= datetime.now(UTC) else export.status
            return {
                "export_id": str(export.id),
                "report_id": export.report_id,
                "status": status,
                "file_path": export.file_path,
                "content_hash": export.content_hash,
                "expires_at": expires_at.isoformat(),
            }

    @staticmethod
    def _ensure_user(session: Session, owner_id: UUID) -> None:
        if session.get(models.UserAccount, owner_id) is None:
            session.add(
                models.UserAccount(
                    id=owner_id,
                    username=f"local-{owner_id.hex[:24]}",
                    password_hash="!managed-by-runtime!",
                    status="ACTIVE",
                    created_at=datetime.now(UTC),
                )
            )
            session.flush()

    @staticmethod
    def _ensure_default_configs(
        session: Session, cost_alias: str, rule_alias: str
    ) -> dict[str, object]:
        created: list[str] = []
        if (
            cost_alias == "cost_v1"
            and session.get(models.CostConfigVersion, _stable_uuid(cost_alias)) is None
        ):
            session.add(
                models.CostConfigVersion(
                    id=_stable_uuid(cost_alias),
                    version=cost_alias,
                    commission_rate="0.00030",
                    commission_min="5.00",
                    stamp_tax_sell_rate="0.00050",
                    transfer_rate="0.00001",
                    regulatory_fee_rate="0",
                    handling_fee_rate="0",
                    commission_includes_regulatory=True,
                    commission_includes_handling=True,
                    slippage_buy="0.002",
                    slippage_sell="0.002",
                    execution_price_mode="NEXT_OPEN_ADJUSTED",
                    partial_fill_mode="FULL_OR_NONE",
                    config={
                        "seed_origin": "local-development",
                        "baseline": "v1.1.0",
                        "review_note": "Local research baseline; verify before production use.",
                    },
                )
            )
            created.append(cost_alias)
        if (
            rule_alias in {"rule_v1", "rule_v2"}
            and session.get(models.RuleConfigVersion, _stable_uuid(rule_alias)) is None
        ):
            is_v2 = rule_alias == "rule_v2"
            session.add(
                models.RuleConfigVersion(
                    id=_stable_uuid(rule_alias),
                    version=rule_alias,
                    scope="A_SHARE_MAIN_BOARD",
                    config={
                        "seed_origin": "local-development",
                        "baseline": "engine-v2" if is_v2 else "v1.1.0",
                        "max_investment_ratio": "0.70",
                        "max_positions": 4,
                        "max_position_ratio": "0.20",
                        "max_industry_ratio": "0.35",
                        "target_position_value": "3500",
                        "lot_size": 100,
                        "drawdown_stop_new": "0.06",
                        "drawdown_review_required": "0.08",
                        "automatic_risk_recovery": False,
                        "review_note": "Local research baseline; verify before production use.",
                    },
                    source_urls=[
                        "https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml",
                        "https://docs.static.szse.cn/www/lawrules/rule/trade/current/W020260424690713155663.pdf",
                    ],
                    effective_from=date(2016, 1, 1),
                    status="PUBLISHED",
                )
            )
            created.append(rule_alias)
        session.flush()
        return {
            "created": bool(created),
            "created_versions": created,
            "cost_version": cost_alias,
            "rule_version": rule_alias,
        }

    @staticmethod
    def _resolve_config_id(session: Session, model: Any, value: str) -> UUID:
        identifier = _uuid(value) or _stable_uuid(value)
        if session.get(model, identifier) is None:
            raise ValueError(f"unknown configuration version: {value}")
        return identifier

    @classmethod
    def _resolve_config(cls, session: Session, model: Any, value: str) -> Any:
        return session.get(model, _uuid(value) or _stable_uuid(value))

    @staticmethod
    def _set_stage(
        session: Session,
        run_id: UUID,
        stage_code: str,
        status: str,
        error_summary: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        stage = session.scalar(
            select(models.RunStage).where(
                models.RunStage.run_id == run_id,
                models.RunStage.stage_code == stage_code,
            )
        )
        if stage is None:
            stage = models.RunStage(
                id=uuid4(),
                run_id=run_id,
                stage_code=stage_code,
                status=status,
                started_at=now if status == "RUNNING" else None,
                ended_at=now if status != "RUNNING" else None,
                error_summary=error_summary,
            )
            session.add(stage)
            return
        stage.status = status
        stage.started_at = stage.started_at or now
        stage.ended_at = now if status != "RUNNING" else None
        stage.error_summary = error_summary

    @staticmethod
    def _result_summary(result: BacktestResult) -> dict[str, object]:
        metrics: dict[str, object] = {"available": result.metrics.available}
        if result.metrics.reason is not None:
            metrics["reason"] = result.metrics.reason
        for name in (
            "total_return",
            "max_drawdown",
            "sharpe",
            "win_rate",
            "profit_loss_ratio",
            "average_holding_period",
            "turnover",
            "total_cost",
        ):
            value = getattr(result.metrics, name)
            if value is not None:
                metrics[name] = str(value)
        metrics["industry_exposure"] = {
            key: str(value) for key, value in result.metrics.industry_exposure
        }
        metrics["monthly_returns"] = {
            key: str(value) for key, value in result.metrics.monthly_returns
        }
        metrics["yearly_returns"] = {
            key: str(value) for key, value in result.metrics.yearly_returns
        }
        full_returns = {
            item.series_code: item.metrics.total_return
            for item in result.segment_metrics
            if item.segment == "FULL" and item.metrics.total_return is not None
        }
        strategy_return = full_returns.get("STRATEGY")
        excess_returns = {
            benchmark: str(strategy_return - benchmark_return)
            for benchmark in ("BENCHMARK_PRIMARY", "UNIVERSE_EQUAL_WEIGHT")
            if strategy_return is not None
            and (benchmark_return := full_returns.get(benchmark)) is not None
        }
        return {
            "available": result.metrics.available,
            "engine_version": result.engine_version,
            "risk_state": result.risk_state,
            "metrics": metrics,
            "excess_returns": excess_returns,
            "equity_curve": [str(value) for value in result.equity_curve],
            "series": {
                item.series_code: {
                    "availability": item.availability,
                    "points": [
                        {"trade_date": trade_date.isoformat(), "value": str(value)}
                        for trade_date, value in item.points
                    ],
                }
                for item in result.series
            },
            "segment_metrics": {
                f"{item.series_code}:{item.segment}": {
                    key: str(value)
                    for key in ("total_return", "max_drawdown", "sharpe")
                    if (value := getattr(item.metrics, key)) is not None
                }
                for item in result.segment_metrics
            },
            "skipped_orders": [
                {
                    "execution_date": item.execution_date.isoformat(),
                    "symbol": item.symbol,
                    "reason": item.reason,
                }
                for item in result.skipped_orders
            ],
            "trades": [
                {
                    "status": fill.status,
                    "side": fill.side,
                    "symbol": fill.symbol,
                    "quantity": fill.quantity,
                    "price": str(fill.price) if fill.price is not None else None,
                    "cost": str(fill.costs.total) if fill.costs is not None else None,
                    "reason": fill.reason,
                    "unfilled_quantity": fill.unfilled_quantity,
                    "execution_date": (
                        fill.execution_date.isoformat() if fill.execution_date is not None else None
                    ),
                    "signal_date": (
                        fill.signal_date.isoformat() if fill.signal_date is not None else None
                    ),
                    "industry": fill.industry,
                    "trigger_reasons": list(fill.trigger_reasons),
                }
                for fill in result.fills
            ],
        }

    @staticmethod
    def _persist_snapshots(
        session: Session,
        run: models.BacktestRun,
        result: BacktestResult,
        trade_dates: tuple[date, ...],
        *,
        warning_threshold: Decimal,
        review_threshold: Decimal,
    ) -> None:
        risk_state = "NORMAL"
        for trade_date, snapshot in zip(trade_dates, result.ledger_snapshots, strict=False):
            if snapshot.drawdown >= review_threshold:
                risk_state = "REVIEW_REQUIRED"
            elif snapshot.drawdown >= warning_threshold and risk_state == "NORMAL":
                risk_state = "STOP_NEW"
            session.add(
                models.PortfolioSnapshot(
                    id=uuid4(),
                    run_id=run.id,
                    account_id=run.created_by,
                    trade_date=trade_date,
                    cash=snapshot.cash,
                    market_value=snapshot.equity - snapshot.cash,
                    equity=snapshot.equity,
                    high_watermark=snapshot.high_water_mark,
                    drawdown=snapshot.drawdown,
                    risk_state=risk_state,
                )
            )

    @staticmethod
    def _persist_series(session: Session, run: models.BacktestRun, result: BacktestResult) -> None:
        for item in result.series:
            for trade_date, value in item.points:
                session.add(
                    models.BacktestSeriesPoint(
                        id=uuid4(),
                        run_id=run.id,
                        series_code=item.series_code,
                        trade_date=trade_date,
                        value=value,
                        availability=item.availability,
                    )
                )

    @staticmethod
    def _persist_metrics(session: Session, run: models.BacktestRun, result: BacktestResult) -> None:
        records = result.segment_metrics or ()
        for record in records:
            values = {
                "total_return": record.metrics.total_return,
                "max_drawdown": record.metrics.max_drawdown,
                "sharpe": record.metrics.sharpe,
            }
            if record.series_code == "STRATEGY":
                values.update(
                    {
                        "win_rate": record.metrics.win_rate,
                        "profit_loss_ratio": record.metrics.profit_loss_ratio,
                        "average_holding_period": record.metrics.average_holding_period,
                        "turnover": record.metrics.turnover,
                        "total_cost": record.metrics.total_cost,
                    }
                )
            for code, value in values.items():
                if value is not None:
                    session.add(
                        models.PerformanceMetric(
                            id=uuid4(),
                            run_id=run.id,
                            series_code=record.series_code,
                            segment=record.segment,
                            metric_code=code,
                            metric_value=value,
                            calculation_note="engine-v2 deterministic event loop",
                        )
                    )

    @classmethod
    def _normalize_daily_bar_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        raw_open = cls._decimal(row.get("raw_open", row.get("open")))
        raw_high = cls._decimal(row.get("raw_high", row.get("high")))
        raw_low = cls._decimal(row.get("raw_low", row.get("low")))
        raw_close = cls._decimal(row.get("raw_close", row.get("close")))
        available_at = cls._timestamp(row.get("available_at"))
        status_observed_at = (
            cls._timestamp(
                row.get(
                    "status_observed_at", row.get("status_available_at", row.get("observed_at"))
                )
            )
            or available_at
        )
        industry_observed_at = (
            cls._timestamp(
                row.get(
                    "industry_observed_at",
                    row.get("industry_available_at", row.get("observed_at")),
                )
            )
            or available_at
        )
        return {
            "symbol": row.get("symbol"),
            "trade_date": cls._as_date(row.get("trade_date")),
            "raw_open": raw_open,
            "raw_high": raw_high,
            "raw_low": raw_low,
            "raw_close": raw_close,
            "adjusted_open": cls._decimal(row.get("adjusted_open", raw_open)),
            "adjusted_high": cls._decimal(row.get("adjusted_high", raw_high)),
            "adjusted_low": cls._decimal(row.get("adjusted_low", raw_low)),
            "adjusted_close": cls._decimal(row.get("adjusted_close", raw_close)),
            "volume": cls._decimal(row.get("volume")),
            "amount": cls._decimal(row.get("amount")),
            "adjust_factor": cls._decimal(row.get("adjust_factor", row.get("adjustment_factor"))),
            "available_at": available_at,
            "status_observed_at": status_observed_at,
            "industry_observed_at": industry_observed_at,
            "open_limit_up": cls._optional_truth_fallback(row, "open_limit_up", "limit_up"),
            "open_limit_down": cls._optional_truth_fallback(row, "open_limit_down", "limit_down"),
            "close_limit_up": cls._optional_truth_fallback(row, "close_limit_up", "limit_up"),
            "close_limit_down": cls._optional_truth_fallback(row, "close_limit_down", "limit_down"),
            "limit_up": cls._optional_truth_fallback(row, "close_limit_up", "limit_up"),
            "limit_down": cls._optional_truth_fallback(row, "close_limit_down", "limit_down"),
            "is_st": cls._truth(row.get("is_st")),
            "is_suspended": cls._truth(row.get("is_suspended"))
            or str(row.get("status", "")).upper() == "SUSPENDED",
            "is_delist_period": cls._truth(row.get("is_delist_period")),
        }

    @staticmethod
    def _status_is_suspended(status: models.SecurityStatusHistory | None) -> bool:
        return bool(status and status.is_suspended)

    @staticmethod
    def _decimal(value: object) -> Any:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            from decimal import Decimal

            return Decimal(str(value))
        except (ValueError, ArithmeticError):
            return value

    @staticmethod
    def _as_date(value: object) -> date | None:
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                return None
        return None

    @staticmethod
    def _timestamp(value: object) -> datetime | None:
        if isinstance(value, datetime):
            return as_utc(value)
        if isinstance(value, str):
            try:
                return as_utc(datetime.fromisoformat(value))
            except ValueError:
                return None
        return None

    @staticmethod
    def _truth(value: object) -> bool:
        return value is True or (isinstance(value, str) and value.lower() in {"true", "1", "yes"})

    @classmethod
    def _optional_truth(cls, row: dict[str, Any], key: str) -> bool | None:
        if key not in row or row.get(key) in (None, ""):
            return None
        return cls._truth(row[key])

    @classmethod
    def _optional_truth_fallback(cls, row: dict[str, Any], key: str, fallback: str) -> bool | None:
        return cls._optional_truth(row, key) if key in row else cls._optional_truth(row, fallback)

    @staticmethod
    def _limit_state(
        bar: models.DailyBar,
        _status: models.SecurityStatusHistory | None,
        _previous: models.DailyBar | None,
    ) -> tuple[bool, bool, bool]:
        """Return only provider-proven daily limit evidence; otherwise fail closed."""
        open_limit_up = getattr(bar, "open_limit_up", None)
        open_limit_down = getattr(bar, "open_limit_down", None)
        if open_limit_up is not None and open_limit_down is not None:
            return bool(open_limit_up), bool(open_limit_down), True
        return False, False, False

    @staticmethod
    def _status_as_of(
        rows: Sequence[models.SecurityStatusHistory],
        trade_date: date,
        information_cutoff_at: datetime,
        batch_priority: dict[UUID, int],
    ) -> models.SecurityStatusHistory | None:
        candidates = [
            row
            for row in rows
            if row.source_batch_id in batch_priority
            and row.effective_date <= trade_date
            and as_utc(row.observed_at) <= as_utc(information_cutoff_at)
        ]
        return (
            max(
                candidates,
                key=lambda item: (
                    item.effective_date,
                    batch_priority[item.source_batch_id],
                ),
            )
            if candidates
            else None
        )

    @staticmethod
    def _industry_as_of(
        rows: Sequence[models.IndustryMembershipHistory],
        trade_date: date,
        information_cutoff_at: datetime,
        batch_priority: dict[UUID, int],
    ) -> tuple[models.IndustryMembershipHistory | None, str | None]:
        candidates = [
            row
            for row in rows
            if row.source_batch_id in batch_priority
            and row.effective_from <= trade_date
            and as_utc(row.observed_at) <= as_utc(information_cutoff_at)
        ]
        if not candidates:
            return None, None
        winning_rank = max(
            (item.effective_from, batch_priority[item.source_batch_id]) for item in candidates
        )
        winners = [
            item
            for item in candidates
            if (item.effective_from, batch_priority[item.source_batch_id]) == winning_rank
        ]
        if len({item.industry_code for item in winners}) != 1:
            return None, "industry history has an unresolved selected-batch tie"
        winner = winners[0]
        if winner.effective_to is not None and trade_date > winner.effective_to:
            return None, None
        return winner, None

    @staticmethod
    def _exchange(symbol: str) -> str:
        suffix = symbol.rsplit(".", 1)[-1].upper()
        return {"SH": "SSE", "SZ": "SZSE"}.get(suffix, suffix or "UNKNOWN")

    @staticmethod
    def _ifind_pick(row: dict[str, Any], *names: str) -> Any:
        for name in names:
            if row.get(name) not in (None, ""):
                return row[name]
        return None

    @staticmethod
    def _normalized_board(value: object) -> str:
        """Translate provider-facing market labels into the strategy board enum."""
        board = str(value or "").strip().upper()
        return {
            "MAIN": "MAIN",
            "MAIN BOARD": "MAIN",
            "MAINBOARD": "MAIN",
            "主板": "MAIN",
            "中小板": "MAIN",
            "GEM": "GEM",
            "创业板": "GEM",
            "STAR": "STAR",
            "科创板": "STAR",
            "BSE": "BSE",
            "北交所": "BSE",
        }.get(board, board or "UNKNOWN")

    @classmethod
    def _ifind_code(cls, row: dict[str, Any]) -> str:
        return str(
            cls._ifind_pick(row, "ts_code", "security_code", "thscode", "symbol", "con_code") or ""
        )

    @staticmethod
    def _strategy_record(strategy: models.StrategyVersion) -> dict[str, Any]:
        definition = get_strategy_definition(strategy.strategy_type)
        return {
            "strategy_version_id": str(strategy.id),
            "owner_id": str(strategy.created_by),
            "name": strategy.code,
            "strategy_type": strategy.strategy_type,
            "status": strategy.status,
            "version": strategy.version,
            "parameters": strategy.parameters,
            "effective_parameters": strategy.parameters,
            "implementation_version": definition.implementation_version,
            "change_reason": strategy.change_reason,
        }

    @staticmethod
    def _run_record(run: models.BacktestRun | None) -> dict[str, Any]:
        if run is None:
            raise ValueError("run is required")
        config_snapshot = run.config_snapshot or {}
        return {
            "run_id": run.run_no,
            "owner_id": str(run.created_by),
            "status": run.status,
            "result_usable": run.result_usable,
            "stages": [],
            "snapshot_hash": run.result_snapshot_hash,
            "result_summary": run.result_summary or {},
            "data_batch_id": str(run.data_batch_id),
            "strategy_version_id": str(run.strategy_version_id),
            "cost_config_id": str(run.cost_config_id),
            "rule_config_id": str(run.rule_config_id),
            "start_date": run.start_date.isoformat(),
            "end_date": run.end_date.isoformat(),
            "created_at": as_utc(run.created_at).isoformat(),
            "data_batch_ids": config_snapshot.get("data_batch_ids", [str(run.data_batch_id)]),
            "strategy_type": config_snapshot.get("strategy_type", "STRONG_TREND"),
            "effective_parameters": config_snapshot.get("effective_parameters", {}),
            "strategy_implementation_version": config_snapshot.get(
                "strategy_implementation_version"
            ),
            "engine_version": config_snapshot.get("engine_version", "engine-v1"),
            "cost_config_hash": config_snapshot.get("cost_config_hash"),
            "data_batch_set_hash": config_snapshot.get("data_batch_set_hash"),
        }

    @staticmethod
    def _daily_bar_record(
        bar: models.DailyBar, security: models.Security, batch: models.DataBatch
    ) -> dict[str, Any]:
        return {
            "symbol": security.symbol,
            "exchange": security.exchange,
            "trade_date": bar.trade_date.isoformat(),
            "raw_open": str(bar.raw_open),
            "raw_high": str(bar.raw_high),
            "raw_low": str(bar.raw_low),
            "raw_close": str(bar.raw_close),
            "adjusted_open": str(bar.adjusted_open),
            "adjusted_high": str(bar.adjusted_high),
            "adjusted_low": str(bar.adjusted_low),
            "adjusted_close": str(bar.adjusted_close),
            "volume": str(bar.volume),
            "amount": str(bar.amount),
            "adjust_factor": str(bar.adjust_factor),
            "available_at": as_utc(bar.available_at).isoformat() if bar.available_at else None,
            "open_limit_up": bar.open_limit_up,
            "open_limit_down": bar.open_limit_down,
            "close_limit_up": bar.close_limit_up,
            "close_limit_down": bar.close_limit_down,
            "limit_up": bar.limit_up,
            "limit_down": bar.limit_down,
            "data_batch_id": str(batch.id),
        }

    @staticmethod
    def _daily_flow_record(run: models.BacktestRun) -> dict[str, Any]:
        summary = run.result_summary or {}
        return {
            "run_id": run.run_no,
            "owner_id": str(run.created_by),
            "status": run.status,
            "result_usable": run.result_usable,
            "data_batch_id": str(run.data_batch_id),
            "strategy_version_id": str(run.strategy_version_id),
            "signal_symbols": summary.get("signal_symbols", []),
            "risk_rejected_symbols": summary.get("risk_rejected_symbols", []),
            "plan_count": summary.get("plan_count", 0),
            "plan_execution_dates": summary.get("plan_execution_dates", []),
            "snapshot_hash": run.result_snapshot_hash,
            "stages": [],
        }

    @staticmethod
    def _plan_record(plan: models.OrderPlan) -> dict[str, Any]:
        return {
            "plan_id": str(plan.id),
            "plan_no": plan.plan_no,
            "run_id": str(plan.run_id),
            "as_of_date": plan.as_of_date.isoformat(),
            "execution_date": plan.execution_date.isoformat(),
            "security_id": str(plan.security_id),
            "side": plan.side,
            "quantity": plan.planned_quantity,
            "reference_low": str(plan.reference_low),
            "reference_high": str(plan.reference_high),
            "estimated_cost": str(plan.estimated_cost),
            "trigger_reasons": plan.trigger_reasons,
            "risk_snapshot": plan.risk_snapshot,
            "status": plan.status,
            "version": plan.version,
        }

    @staticmethod
    def _page(items: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
        start = (page - 1) * page_size
        return {
            "items": items[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": len(items),
        }

    @staticmethod
    def _batch_record(
        batch: models.DataBatch, source_name: str, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        quality_summary = batch.quality_summary or {}
        original = payload or {}
        return {
            "batch_id": str(batch.id),
            "owner_id": batch.owner_id,
            "quality_status": batch.status,
            "data_date": batch.as_of_date.isoformat(),
            "source_name": source_name,
            "data_type": batch.dataset_type,
            "version": batch.version,
            "record_count": batch.record_count or quality_summary.get("record_count", 0),
            "quality_summary": quality_summary,
            "file_location": batch.file_location or original.get("file_location"),
            "license_note": batch.license_note or original.get("license_note"),
            "available_at": batch.available_at.isoformat(),
            "information_cutoff_at": batch.information_cutoff_at.isoformat(),
            "content_hash": batch.content_hash,
            "file_hash": batch.file_hash or batch.content_hash,
            "start_date": (
                batch.start_date.isoformat()
                if batch.start_date is not None
                else batch.as_of_date.isoformat()
            ),
            "end_date": (
                batch.end_date.isoformat()
                if batch.end_date is not None
                else batch.as_of_date.isoformat()
            ),
        }


__all__ = ["SqlAlchemyResearchRepository"]
