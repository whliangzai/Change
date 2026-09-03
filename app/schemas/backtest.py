from datetime import date

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import MoneyString


class BacktestCreate(BaseModel):
    data_batch_id: str = Field(min_length=1)
    strategy_version_id: str = Field(min_length=1)
    cost_config_id: str = Field(min_length=1)
    rule_config_id: str = Field(min_length=1)
    start_date: date
    end_date: date
    train_end: date
    valid_end: date
    oos_start: date
    benchmark_symbol: str = Field(min_length=1, max_length=32)
    initial_equity: MoneyString
    mode: str = Field(default="BACKTEST", pattern="^BACKTEST$")

    @model_validator(mode="after")
    def validate_split(self) -> "BacktestCreate":
        if not self.start_date <= self.train_end < self.valid_end < self.oos_start <= self.end_date:
            raise ValueError("invalid train/validation/out-of-sample date split")
        return self
