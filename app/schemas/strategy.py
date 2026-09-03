from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    change_reason: str = Field(min_length=1, max_length=1024)
    parameters: dict[str, object] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, object]) -> dict[str, object]:
        lower = value.get("return_lower")
        upper = value.get("return_upper")
        if lower is not None and upper is not None and Decimal(str(lower)) > Decimal(str(upper)):
            raise ValueError("return_lower must not exceed return_upper")
        return value


class StrategyReview(BaseModel):
    decision: str = Field(default="PUBLISH", pattern="^(SUBMIT|PUBLISH|REJECT)$")
    review_note: str = Field(min_length=1, max_length=2048)
