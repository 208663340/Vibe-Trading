"""Regression tests for analytic option payoff and scenario calculations."""

from __future__ import annotations

import numpy as np
import pytest

from src.backtest.engines.options_portfolio import bs_price
from src.backtest.options_payoff import (
    OptionLeg,
    bull_call_spread,
    default_spot_grid,
    expiry_payoff,
    iron_condor,
    long_straddle,
    scenario_grid,
)

RATE = 0.05
IV = 0.3
TIME_TO_EXPIRY = 0.5


def _grid(center: float = 100.0) -> np.ndarray:
    """Return a compact display grid for tests."""
    return default_spot_grid(center, 0.6, points=401)


def test_long_call_breakeven_and_payoff() -> None:
    """A long call has premium-limited loss and unbounded upside."""
    leg = OptionLeg("call", 100.0, 1)
    report = expiry_payoff(
        [leg],
        _grid(),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        rate=RATE,
        iv=IV,
        commission_rate=0.0,
    )

    premium = bs_price(100.0, 100.0, TIME_TO_EXPIRY, RATE, IV, "call")
    assert report.net_premium == pytest.approx(premium, rel=1e-9)
    assert report.entry_commission == 0.0
    assert report.breakevens == pytest.approx([100.0 + premium], abs=1e-6)
    assert report.payoff[-1] == pytest.approx(report.spot_grid[-1] - 100.0 - premium, rel=1e-9)
    assert report.profit_unbounded is True
    assert report.loss_unbounded is False
    assert report.max_loss == pytest.approx(-premium, rel=1e-9)


def test_short_put_breakeven_and_zero_spot_floor() -> None:
    """A naked short put's finite floor is solved at physically valid spot zero."""
    leg = OptionLeg("put", 100.0, -1)
    report = expiry_payoff(
        [leg],
        np.array([80.0, 120.0]),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        rate=RATE,
        iv=IV,
        commission_rate=0.0,
    )

    premium = bs_price(100.0, 100.0, TIME_TO_EXPIRY, RATE, IV, "put")
    assert report.breakevens == pytest.approx([100.0 - premium], abs=1e-6)
    assert report.max_loss == pytest.approx(premium - 100.0, rel=1e-9)
    assert report.max_profit == pytest.approx(premium, rel=1e-9)


def test_bull_call_spread_shape() -> None:
    """A bull call spread has analytic capped loss, profit, and one root."""
    legs = bull_call_spread(95.0, 105.0)
    report = expiry_payoff(
        legs,
        _grid(),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        rate=RATE,
        iv=IV,
        commission_rate=0.0,
    )

    debit = bs_price(100.0, 95.0, TIME_TO_EXPIRY, RATE, IV, "call") - bs_price(
        100.0, 105.0, TIME_TO_EXPIRY, RATE, IV, "call"
    )
    assert report.max_profit == pytest.approx(10.0 - debit, abs=1e-6)
    assert report.max_loss == pytest.approx(-debit, abs=1e-6)
    assert report.breakevens == pytest.approx([95.0 + debit], abs=1e-6)


def test_extrema_do_not_depend_on_display_grid_containing_strikes() -> None:
    """A butterfly peak at an omitted strike is still included analytically."""
    legs = [
        OptionLeg("call", 90.0, 1, premium=0.0),
        OptionLeg("call", 100.0, -2, premium=0.0),
        OptionLeg("call", 110.0, 1, premium=0.0),
    ]
    report = expiry_payoff(
        legs,
        np.array([80.0, 120.0]),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        commission_rate=0.0,
    )

    assert report.payoff.tolist() == pytest.approx([0.0, 0.0])
    assert report.max_profit == pytest.approx(10.0)
    assert report.max_loss == pytest.approx(0.0)
    assert report.breakevens == []
    assert report.breakeven_intervals == [(0.0, 90.0), (110.0, None)]


def test_breakeven_can_sit_outside_display_grid() -> None:
    """Right-tail roots are solved analytically beyond a narrow chart grid."""
    report = expiry_payoff(
        [OptionLeg("call", 100.0, 1, premium=10.0)],
        np.array([80.0, 90.0]),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        commission_rate=0.0,
    )

    assert report.breakevens == pytest.approx([110.0])


def test_entry_commission_matches_options_engine_cash_semantics() -> None:
    """Long and short entry fees both increase signed strategy entry cost."""
    legs = [
        OptionLeg("call", 95.0, 1, premium=8.0),
        OptionLeg("call", 105.0, -1, premium=3.0),
    ]
    report = expiry_payoff(
        legs,
        _grid(),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        multiplier=100.0,
        commission_rate=0.001,
    )

    assert report.net_premium == pytest.approx(500.0)
    assert report.entry_commission == pytest.approx(1.1)
    assert report.entry_cost == pytest.approx(501.1)
    assert report.breakevens == pytest.approx([100.011])
    assert report.max_loss == pytest.approx(-501.1)
    assert report.max_profit == pytest.approx(498.9)


def test_long_straddle_two_breakevens() -> None:
    """A long straddle has one root on either side of its strike."""
    legs = long_straddle(100.0)
    report = expiry_payoff(
        legs,
        _grid(),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        rate=RATE,
        iv=IV,
        commission_rate=0.0,
    )

    cost = bs_price(100.0, 100.0, TIME_TO_EXPIRY, RATE, IV, "call") + bs_price(
        100.0, 100.0, TIME_TO_EXPIRY, RATE, IV, "put"
    )
    assert report.breakevens == pytest.approx([100.0 - cost, 100.0 + cost], abs=1e-6)


def test_iron_condor_max_profit_inside_body() -> None:
    """A short iron condor keeps its entry credit between the body strikes."""
    legs = iron_condor(90.0, 95.0, 105.0, 110.0)
    report = expiry_payoff(
        legs,
        _grid(),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        rate=RATE,
        iv=IV,
        commission_rate=0.0,
    )

    body = (report.spot_grid >= 95.0) & (report.spot_grid <= 105.0)
    assert np.allclose(report.payoff[body], -report.net_premium)
    assert report.net_premium < 0
    assert report.max_profit == pytest.approx(-report.net_premium, abs=1e-6)


def test_naked_short_call_marks_unbounded_loss() -> None:
    """A naked short call uses an explicit unbounded-loss marker."""
    report = expiry_payoff(
        [OptionLeg("call", 100.0, -1)],
        _grid(),
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        commission_rate=0.0,
    )

    assert report.loss_unbounded is True
    assert report.max_loss == float("-inf")
    assert report.profit_unbounded is False


def test_scenario_entry_cell_equals_negative_entry_commission() -> None:
    """The entry scenario includes the same opening fee as the engine."""
    legs = long_straddle(100.0)
    spots = np.array([95.0, 100.0, 105.0])
    ivs = np.array([0.2, IV, 0.4])
    commission_rate = 0.001
    grid = scenario_grid(
        legs,
        spots,
        ivs,
        entry_spot=100.0,
        time_to_expiry=TIME_TO_EXPIRY,
        rate=RATE,
        entry_iv=IV,
        commission_rate=commission_rate,
    )

    gross = bs_price(100.0, 100.0, TIME_TO_EXPIRY, RATE, IV, "call") + bs_price(
        100.0, 100.0, TIME_TO_EXPIRY, RATE, IV, "put"
    )
    assert grid[1, 1] == pytest.approx(-gross * commission_rate, abs=1e-9)
    assert grid[2, 1] > grid[1, 1]
    assert grid[0, 1] < grid[1, 1]


@pytest.mark.parametrize(
    "legs, grid, error",
    [
        ([], np.array([100.0]), "at least one leg"),
        ([OptionLeg("strangle", 100.0, 1)], np.array([100.0]), "option_type"),
        ([OptionLeg("call", -1.0, 1)], np.array([100.0]), "strike"),
        ([OptionLeg("call", 100.0, 0)], np.array([100.0]), "qty"),
        ([OptionLeg("call", 100.0, 1)], np.array([-1.0]), "non-negative"),
    ],
)
def test_rejects_invalid_legs_and_grids(legs: list[OptionLeg], grid: np.ndarray, error: str) -> None:
    """Invalid strategy and chart inputs fail before numeric calculation."""
    with pytest.raises(ValueError, match=error):
        expiry_payoff(
            legs,
            grid,
            entry_spot=100.0,
            time_to_expiry=TIME_TO_EXPIRY,
        )


def test_preset_validations() -> None:
    """Strategy helpers reject reversed strikes and non-positive quantities."""
    with pytest.raises(ValueError):
        bull_call_spread(105.0, 95.0)
    with pytest.raises(ValueError):
        iron_condor(95.0, 90.0, 105.0, 110.0)
    with pytest.raises(ValueError):
        long_straddle(100.0, qty=0)
