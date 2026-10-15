"""Regression: format_grounding_block must not crash on NaN/inf volume or close.

Loaders can return NaN for missing OHLCV fields (e.g. volume on a
non-trading day, close on a halted stock). The original code called
``int(row['volume'])`` unconditionally — ``int(float('nan'))`` raises
``ValueError: cannot convert float NaN to integer``. NaN closes also
produced ``nan`` in the formatted output via ``:.2f``.
"""

from __future__ import annotations

import math

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "grounding_test_mod",
    Path(__file__).resolve().parent.parent / "src" / "runtime" / "swarm" / "grounding.py",
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["grounding_test_mod"] = _mod
_spec.loader.exec_module(_mod)
format_grounding_block = _mod.format_grounding_block


def test_format_with_normal_data() -> None:
    """Normal data renders without error."""
    grounding = {
        "AAPL.US": [
            {"trade_date": "2026-01-01", "open": 150.0, "high": 155.0,
             "low": 149.0, "close": 152.0, "volume": 1_000_000.0},
            {"trade_date": "2026-01-02", "open": 152.0, "high": 158.0,
             "low": 151.0, "close": 157.0, "volume": 1_200_000.0},
        ],
    }
    result = format_grounding_block(grounding)
    assert "AAPL.US" in result
    assert "152.00" in result
    assert "1,000,000" in result


def test_format_with_nan_volume_does_not_crash() -> None:
    """NaN volume must render as a dash, not crash with ValueError."""
    grounding = {
        "AAPL.US": [
            {"trade_date": "2026-01-01", "open": 150.0, "high": 155.0,
             "low": 149.0, "close": 152.0, "volume": float("nan")},
            {"trade_date": "2026-01-02", "open": 152.0, "high": 158.0,
             "low": 151.0, "close": 157.0, "volume": 1_200_000.0},
        ],
    }
    result = format_grounding_block(grounding)
    assert "AAPL.US" in result
    assert "—" in result  # NaN volume rendered as dash


def test_format_with_nan_close_does_not_crash() -> None:
    """NaN close must render as a dash, not produce 'nan' in the table."""
    grounding = {
        "AAPL.US": [
            {"trade_date": "2026-01-01", "open": 150.0, "high": 155.0,
             "low": 149.0, "close": float("nan"), "volume": 1_000_000.0},
            {"trade_date": "2026-01-02", "open": 152.0, "high": 158.0,
             "low": 151.0, "close": 157.0, "volume": 1_200_000.0},
        ],
    }
    result = format_grounding_block(grounding)
    assert "AAPL.US" in result
    # The NaN close row should have a dash, not 'nan'.
    assert "nan" not in result.lower()


def test_format_with_all_nan_closes_skips_symbol() -> None:
    """When all closes are NaN, the symbol is skipped entirely."""
    grounding = {
        "AAPL.US": [
            {"trade_date": "2026-01-01", "open": 150.0, "high": 155.0,
             "low": 149.0, "close": float("nan"), "volume": 1_000_000.0},
        ],
    }
    result = format_grounding_block(grounding)
    # No data to render → empty string.
    assert result == ""


def test_format_with_inf_volume_does_not_crash() -> None:
    """inf volume must render as a dash, not crash."""
    grounding = {
        "AAPL.US": [
            {"trade_date": "2026-01-01", "open": 150.0, "high": 155.0,
             "low": 149.0, "close": 152.0, "volume": float("inf")},
            {"trade_date": "2026-01-02", "open": 152.0, "high": 158.0,
             "low": 151.0, "close": 157.0, "volume": 1_200_000.0},
        ],
    }
    result = format_grounding_block(grounding)
    assert "AAPL.US" in result
    assert "—" in result


def test_format_with_empty_grounding() -> None:
    assert format_grounding_block({}) == ""


def test_format_with_empty_rows() -> None:
    result = format_grounding_block({"AAPL.US": []})
    assert result == ""


def test_latest_close_date_matches_finite_close_row() -> None:
    """When the final bar has NaN close, the 'Latest close' date must match
    the last row with a finite close, not the final bar's date."""
    grounding = {
        "AAPL.US": [
            {"trade_date": "2026-08-05", "close": 195.0, "volume": 1000000},
            {"trade_date": "2026-08-06", "close": 200.0, "volume": 1100000},
            {"trade_date": "2026-08-07", "close": float("nan"), "volume": 0},
        ]
    }
    result = format_grounding_block(grounding)
    # Latest close should be 200.00 with date 2026-08-06, not 2026-08-07
    assert "200.00 (2026-08-06)" in result
    assert "200.00 (2026-08-07)" not in result
