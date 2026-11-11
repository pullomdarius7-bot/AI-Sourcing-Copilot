import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.analysis import units


class FxRateIn(BaseModel):
    base: str = Field(pattern=r"^[A-Za-z]{3}$")
    quote: str = Field(pattern=r"^[A-Za-z]{3}$")
    rate: Decimal = Field(gt=0, max_digits=24, decimal_places=10)
    valid_from: date
    source: str | None = Field(default=None, max_length=200)

    @field_validator("base", "quote")
    @classmethod
    def upper(cls, value: str) -> str:
        return value.upper()


class FxRateOut(BaseModel):
    id: uuid.UUID
    base: str
    quote: str
    rate: Decimal
    valid_from: date
    source: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class UnitConversionIn(BaseModel):
    from_unit: str = Field(min_length=1, max_length=30)
    to_unit: str = Field(min_length=1, max_length=30)
    factor: Decimal = Field(gt=0, max_digits=30, decimal_places=12)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("from_unit", "to_unit")
    @classmethod
    def canonical(cls, value: str) -> str:
        return units.canonical(value) or value.strip().lower()


class UnitConversionOut(BaseModel):
    id: uuid.UUID
    from_unit: str
    to_unit: str
    factor: Decimal
    note: str | None
    created_by: uuid.UUID | None
    created_at: datetime


class UnitInfo(BaseModel):
    unit: str
    dimension: str
    to_base: str
    aliases: list[str]


class ConvertCheckOut(BaseModel):
    from_unit: str
    to_unit: str
    factor: str | None
    path: str | None
    custom: bool
