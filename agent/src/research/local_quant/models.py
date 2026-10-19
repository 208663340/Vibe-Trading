"""Bounded, path-free requests exposed to the agent."""

from __future__ import annotations

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Literal

MAIN_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")


class CloseScreen(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    min_close: float = Field(default=0.01, ge=0, strict=True)
    max_close: float | None = Field(default=None, gt=0, strict=True)
    min_volume: float = Field(default=1, ge=0, strict=True)
    ma_window: int | None = Field(default=20, ge=2, le=250, strict=True)
    min_momentum20: float | None = Field(default=None, ge=-1, le=100, strict=True)
    min_turnover: float | None = Field(default=None, ge=0, le=1000, strict=True)
    max_turnover: float | None = Field(default=None, ge=0, le=1000, strict=True)
    rank_by: Literal["momentum20", "volume", "turnover"] = "momentum20"
    top_n: int = Field(default=50, ge=1, le=200, strict=True)

    @model_validator(mode="after")
    def ordered_bounds(self):
        if self.max_close is not None and self.max_close < self.min_close:
            raise ValueError("max_close must be at least min_close")
        if self.min_turnover is not None and self.max_turnover is not None and self.max_turnover < self.min_turnover:
            raise ValueError("max_turnover must be at least min_turnover")
        return self


class QuantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    strategy_id: str = Field(min_length=1, max_length=60)
    start_date: date
    end_date: date
    codes: list[str] | None = Field(default=None, min_length=1, max_length=500)
    data_quality: Literal["strict", "quarantine"] = "strict"
    screen: CloseScreen = Field(default_factory=CloseScreen)
    max_positions: int = Field(default=10, ge=1, le=100, strict=True)
    initial_cash: float = Field(default=1_000_000, gt=0, le=1e12, strict=True)
    commission_rate: float = Field(default=0.00025, ge=0, le=0.1, strict=True)
    commission_min: float = Field(default=5, ge=0, le=10000, strict=True)
    stamp_tax: float = Field(default=0.0005, ge=0, le=0.1, strict=True)
    transfer_fee: float = Field(default=0.00001, ge=0, le=0.1, strict=True)
    slippage: float = Field(default=0.001, ge=0, le=0.1, strict=True)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def strict_dates(cls, value):
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return date.fromisoformat(value)
        if type(value) is date:
            return value
        raise ValueError("dates must use YYYY-MM-DD")

    @field_validator("codes")
    @classmethod
    def main_board_codes(cls, value):
        if value is None:
            return None
        out = []
        for raw in value:
            if not re.fullmatch(r"\d{6}(?:\.(?:SH|SZ))?", raw):
                raise ValueError("codes must be six-digit A-share main-board symbols")
            code = raw[:6]
            suffix = "SH" if code.startswith("6") else "SZ"
            if not code.startswith(MAIN_PREFIXES) or ("." in raw and raw[7:] != suffix):
                raise ValueError("only correctly suffixed A-share main-board codes are supported")
            out.append(code)
        return list(dict.fromkeys(out))

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        if (self.end_date - self.start_date).days > 3660:
            raise ValueError("evaluation window must not exceed ten years")
        return self
