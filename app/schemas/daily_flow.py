from datetime import date, datetime

from pydantic import BaseModel, Field

from app.schemas.common import MoneyString


class DailyFlowCreate(BaseModel):
    data_batch_id: str = Field(min_length=1)
    strategy_version_id: str = Field(min_length=1)
    cost_config_id: str = Field(default="cost_v1", min_length=1)
    rule_config_id: str = Field(default="rule_v1", min_length=1)
    as_of_date: date
    information_cutoff_at: datetime
    benchmark_symbol: str = Field(default="000300.SH", min_length=1, max_length=32)
    initial_equity: MoneyString = "20000.00"
    max_investment_ratio: MoneyString = "0.70"
