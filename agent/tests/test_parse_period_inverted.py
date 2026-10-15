"""Regression: inverted period strings must raise ValueError."""

from __future__ import annotations

import pytest

from src.runtime.tools.alpha_bench_tool import _parse_period


def test_parse_period_rejects_inverted_year_range() -> None:
    with pytest.raises(ValueError, match="start_date"):
        _parse_period("2024-2020")


def test_parse_period_rejects_inverted_date_range() -> None:
    with pytest.raises(ValueError, match="start_date"):
        _parse_period("2020-01-02/2020-01-01")


def test_parse_period_normal_ranges_still_work() -> None:
    assert _parse_period("2020-2024") == ("2020-01-01", "2024-12-31")
    assert _parse_period("2020-01-01/2020-12-31") == ("2020-01-01", "2020-12-31")
