"""Inverted journal date filters must raise, not silently empty the frame."""

from __future__ import annotations

import pandas as pd
import pytest

from src.runtime.tools.trade_journal_tool import _apply_filter


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-01-15", "2026-02-15", "2026-03-15"]),
            "symbol": ["A", "A", "A"],
        }
    )


def test_inverted_month_filter_raises() -> None:
    with pytest.raises(ValueError, match="inverted date filter"):
        _apply_filter(_frame(), "2026-03 to 2026-01")


def test_normal_month_filter_keeps_rows() -> None:
    out = _apply_filter(_frame(), "2026-01 to 2026-02")
    assert list(out["datetime"].dt.strftime("%Y-%m")) == ["2026-01", "2026-02"]
