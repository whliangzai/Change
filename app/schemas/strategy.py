from pydantic import BaseModel, Field, model_validator

from app.domain.strategy.registry import StrategyType, expand_parameters


class StrategyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    change_reason: str = Field(min_length=1, max_length=1024)
    strategy_type: StrategyType = StrategyType.STRONG_TREND
    parameters: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_parameters(self) -> "StrategyCreate":
        self.parameters = expand_parameters(self.strategy_type.value, self.parameters)
        return self


class StrategyReview(BaseModel):
    decision: str = Field(default="PUBLISH", pattern="^(SUBMIT|PUBLISH|REJECT)$")
    review_note: str = Field(min_length=1, max_length=2048)
