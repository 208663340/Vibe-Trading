"""Treat non-positive spot/strike as intrinsic in Black-Scholes helpers."""

from __future__ import annotations

from src.backtest.engines.options_portfolio import bs_greeks, bs_price


def test_bs_price_zero_strike_returns_intrinsic() -> None:
    # Call with K=0 is deep ITM; intrinsic is max(S-K, 0) == S.
    assert bs_price(100.0, 0.0, 1.0, 0.05, 0.2, "call") == 100.0
    assert bs_price(100.0, 0.0, 1.0, 0.05, 0.2, "put") == 0.0


def test_bs_greeks_zero_strike_returns_degenerate_greeks() -> None:
    g = bs_greeks(100.0, 0.0, 1.0, 0.05, 0.2, "call")
    assert g["delta"] == 1.0
    assert g["gamma"] == 0.0
    assert g["theta"] == 0.0
    assert g["vega"] == 0.0


def test_bs_price_positive_strike_unchanged() -> None:
    price = bs_price(100.0, 100.0, 1.0, 0.05, 0.2, "call")
    assert abs(price - 10.45) < 0.05
