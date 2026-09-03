from datetime import date

from pydantic import BaseModel, Field


class BatchCreate(BaseModel):
    source_name: str = Field(min_length=1, max_length=128)
    data_type: str = Field(min_length=1, max_length=64)
    file_location: str = Field(min_length=1, max_length=1024)
    license_note: str = Field(min_length=1, max_length=2048)
    date_from: date | None = None
    date_to: date | None = None


class PoolQuery(BaseModel):
    trade_date: date
    status: str | None = None
