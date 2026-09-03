from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def _money(value: str) -> str:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("must be a finite decimal string") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("must be a non-negative decimal string")
    return value


MoneyString = Annotated[str, AfterValidator(_money)]


class PageQuery(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class Page(BaseModel):
    items: list[dict[str, object]]
    page: int
    page_size: int
    total: int
