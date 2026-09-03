from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import MoneyString


class PlanDecision(BaseModel):
    decision: str = Field(pattern="^(CONFIRM|SKIP)$")
    review_note: str = Field(min_length=1, max_length=2048)
    expected_version: int = Field(ge=1)


class ExecutionCreate(BaseModel):
    plan_id: str = Field(min_length=1)
    execution_type: str = Field(default="MANUAL_ENTRY", pattern="^MANUAL_ENTRY$")
    executed_at: datetime
    quantity: int = Field(gt=0)
    price: MoneyString
    commission: MoneyString
    stamp_tax: MoneyString
    transfer_fee: MoneyString
    other_fee: MoneyString
    unfilled_quantity: int = Field(ge=0)
    note: str = Field(min_length=1, max_length=2048)

    @model_validator(mode="after")
    def validate_fill(self) -> "ExecutionCreate":
        if self.unfilled_quantity < 0:
            raise ValueError("unfilled_quantity must be non-negative")
        return self


class ExecutionQuery(BaseModel):
    execution_date: date | None = None
