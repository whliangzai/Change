"""Closed strategy registry and typed, fully-expanded parameter contracts."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.strategy.ma_trend import MATrendConfig, MATrendStrategy
from app.domain.strategy.strong_trend import StrongTrendConfig, StrongTrendStrategy


class StrategyType(StrEnum):
    STRONG_TREND = "STRONG_TREND"
    MA_TREND = "MA_TREND"


_STRATEGY_PRESENTATION = {
    StrategyType.STRONG_TREND: {
        "display_name": "强势趋势延续",
        "description": "保留现有过滤、评分和动态退出规则，可用于回测与日终。",
    },
    StrategyType.MA_TREND: {
        "display_name": "MA20/MA60 均线趋势",
        "description": "基于短长期均线关系和市场过滤的趋势策略，仅允许回测。",
    },
}


class StrongTrendParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_positions: int = Field(
        default=4, ge=1, le=20, title="最大持仓数", description="组合同时持有的股票数量上限。"
    )
    max_per_industry: int = Field(
        default=2,
        ge=1,
        le=20,
        title="单行业最多持仓",
        description="同一行业允许同时持有的股票数量上限。",
    )
    min_return_5d: Decimal = Field(
        default=Decimal("0.02"),
        ge=Decimal("-1"),
        le=Decimal("5"),
        title="5日涨幅下限",
        description="候选股票最近 5 个交易日收益率下限，例如 0.02 表示 2%。",
    )
    max_return_5d: Decimal = Field(
        default=Decimal("0.12"),
        ge=Decimal("-1"),
        le=Decimal("5"),
        title="5日涨幅上限",
        description="候选股票最近 5 个交易日收益率上限，例如 0.12 表示 12%。",
    )
    min_amount_ratio: Decimal = Field(
        default=Decimal("1.2"),
        ge=Decimal("0"),
        le=Decimal("100"),
        title="成交额比下限",
        description="当日成交额相对历史均值的最低倍数。",
    )
    max_amount_ratio: Decimal = Field(
        default=Decimal("3.0"),
        ge=Decimal("0"),
        le=Decimal("100"),
        title="成交额比上限",
        description="当日成交额相对历史均值的最高倍数。",
    )
    max_holding_days: int = Field(
        default=3,
        ge=1,
        le=252,
        title="最大持有交易日",
        description="达到该交易日数后生成退出信号。",
    )
    drawdown_exit: Decimal = Field(
        default=Decimal("0.04"),
        gt=Decimal("0"),
        lt=Decimal("1"),
        title="入场价回撤退出阈值",
        description="相对实际入场成交价的回撤阈值；必须大于 0 且小于 1。",
    )


class MATrendParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    short_window: int = Field(
        default=20, ge=2, le=250, title="短期均线窗口", description="个股短期均线使用的交易日数量。"
    )
    long_window: int = Field(
        default=60,
        ge=3,
        le=500,
        title="长期均线窗口",
        description="个股与市场长期均线使用的交易日数量。",
    )
    max_positions: int = Field(
        default=4, ge=1, le=20, title="最大持仓数", description="组合同时持有的股票数量上限。"
    )
    max_per_industry: int = Field(
        default=2,
        ge=1,
        le=20,
        title="单行业最多持仓",
        description="同一行业允许同时持有的股票数量上限。",
    )
    max_holding_days: int = Field(
        default=20,
        ge=1,
        le=252,
        title="最大持有交易日",
        description="达到该交易日数后生成退出信号。",
    )
    require_market_above_long_ma: bool = Field(
        default=True,
        title="启用市场长期均线过滤",
        description="启用后，仅在市场基准收盘价高于长期均线时允许产生入场信号。",
    )


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    strategy_type: StrategyType
    parameter_model: type[BaseModel]
    minimum_history: int
    supports_backtest: bool
    approved_for_daily: bool
    implementation_version: str

    def validate_parameters(self, parameters: dict[str, object]) -> BaseModel:
        result = self.parameter_model.model_validate(parameters)
        if isinstance(result, StrongTrendParameters):
            if result.min_return_5d > result.max_return_5d:
                raise ValueError("min_return_5d must not exceed max_return_5d")
            if result.min_amount_ratio > result.max_amount_ratio:
                raise ValueError("min_amount_ratio must not exceed max_amount_ratio")
        if isinstance(result, MATrendParameters) and result.short_window >= result.long_window:
            raise ValueError("short_window must be less than long_window")
        return result

    def build(self, parameters: dict[str, object]) -> StrongTrendStrategy | MATrendStrategy:
        validated = self.validate_parameters(parameters)
        if isinstance(validated, StrongTrendParameters):
            return StrongTrendStrategy(StrongTrendConfig(**validated.model_dump()))
        assert isinstance(validated, MATrendParameters)
        return MATrendStrategy(MATrendConfig(**validated.model_dump()))

    def catalog_record(self) -> dict[str, Any]:
        defaults = self.parameter_model().model_dump(mode="json")
        schema = self.parameter_model.model_json_schema()
        return {
            "strategy_type": self.strategy_type.value,
            **_STRATEGY_PRESENTATION[self.strategy_type],
            "default_parameters": defaults,
            "parameter_schema": schema,
            "minimum_history": self.minimum_history,
            "supports_backtest": self.supports_backtest,
            "approved_for_daily": self.approved_for_daily,
            "implementation_version": self.implementation_version,
        }


STRATEGY_REGISTRY: dict[StrategyType, StrategyDefinition] = {
    StrategyType.STRONG_TREND: StrategyDefinition(
        StrategyType.STRONG_TREND,
        StrongTrendParameters,
        21,
        True,
        True,
        "strong-trend-v2",
    ),
    StrategyType.MA_TREND: StrategyDefinition(
        StrategyType.MA_TREND,
        MATrendParameters,
        60,
        True,
        False,
        "ma-trend-v1",
    ),
}


def get_strategy_definition(strategy_type: str | StrategyType) -> StrategyDefinition:
    try:
        key = StrategyType(strategy_type)
    except ValueError as exc:
        raise ValueError(f"unknown strategy_type: {strategy_type}") from exc
    return STRATEGY_REGISTRY[key]


def expand_parameters(strategy_type: str, parameters: dict[str, object]) -> dict[str, object]:
    definition = get_strategy_definition(strategy_type)
    return definition.validate_parameters(parameters).model_dump(mode="json")


def strategy_catalog() -> list[dict[str, Any]]:
    return [definition.catalog_record() for definition in STRATEGY_REGISTRY.values()]


__all__ = [
    "MATrendParameters",
    "STRATEGY_REGISTRY",
    "StrategyDefinition",
    "StrategyType",
    "StrongTrendParameters",
    "expand_parameters",
    "get_strategy_definition",
    "strategy_catalog",
]
