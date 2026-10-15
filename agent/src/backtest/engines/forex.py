"""Forex (FX spot / CFD) backtest engine.

Market rules:
  - 24x5 (Mon Sydney open to Fri NYC close)
  - Spread replaces explicit commission (bid-ask)
  - Leverage: 50:1 to 500:1 (configurable)
  - Standard lot = 100,000 units of base currency (metals differ: see _METAL_SPECS)
  - Swap (overnight rollover interest) at daily close
  - No price limits, no restrictions on direction
  - PnL in quote currency (converted via exit price for cross pairs)
"""

from __future__ import annotations

import pandas as pd

from src.backtest.engines.base import BaseEngine
# ``_normalize_symbol`` lives in ``_market_hooks`` (single source of truth);
# re-imported here so external callers (tests) keep their existing import path.
from src.backtest.engines._market_hooks import _normalize_symbol, calc_forex_swap


# ── Typical spreads in pips (1 pip = 0.0001 for most pairs, 0.01 for JPY) ──

_SPREAD_PIPS: dict[str, float] = {
    # Majors
    "EUR/USD": 1.0, "GBP/USD": 1.2, "USD/JPY": 1.0, "USD/CHF": 1.3,
    "AUD/USD": 1.2, "USD/CAD": 1.5, "NZD/USD": 1.5,
    # Crosses
    "EUR/GBP": 1.5, "EUR/JPY": 1.5, "GBP/JPY": 2.5, "EUR/CHF": 1.8,
    "AUD/JPY": 2.0, "CHF/JPY": 2.5, "EUR/AUD": 2.0, "GBP/AUD": 3.0,
    "EUR/CAD": 2.5, "GBP/CAD": 3.5, "AUD/CAD": 2.5, "NZD/JPY": 2.5,
    # Exotics (wider spreads)
    "USD/TRY": 15.0, "USD/ZAR": 10.0, "USD/MXN": 8.0,
    "USD/SGD": 3.0, "USD/HKD": 3.0, "USD/CNH": 5.0,
    # Metals, in that metal's own pips (see _METAL_SPECS). The XAU/USD figure is
    # the median measured from Dukascopy tick data, 2017-2024 (~$0.32); the rest
    # are typical retail quotes and should be overridden if you have better data.
    "XAU/USD": 3.2, "XAG/USD": 2.5, "XPT/USD": 20.0, "XPD/USD": 30.0,
}
_DEFAULT_SPREAD_PIPS = 2.0

# Standard lot size (FX pairs)
STANDARD_LOT = 100_000

# ── Metals quote and size differently from FX pairs ──
#
# XAU/USD is not a 0.0001-pip, 100,000-unit instrument: one pip is $0.10 and one
# standard lot is 100 ounces. Treating it as a generic FX pair understates the
# spread by ~1000x and rounds any position under 1,000 oz down to zero.
#
# base -> (pip size in price terms, units per standard lot)
_METAL_SPECS: dict[str, tuple[float, float]] = {
    "XAU": (0.10, 100.0),      # gold: 1 lot = 100 troy oz
    "XAG": (0.01, 5_000.0),    # silver: 1 lot = 5,000 troy oz
    "XPT": (0.10, 100.0),      # platinum
    "XPD": (0.10, 100.0),      # palladium
}


def _metal_base(symbol: str) -> str | None:
    """Return the metal code ('XAU', ...) if this is a metal pair, else None."""
    base = (symbol.split("/")[0] if "/" in symbol else symbol[:3]).upper()
    return base if base in _METAL_SPECS else None


def _pip_value(symbol: str) -> float:
    """Size of 1 pip for the pair.

    Args:
        symbol: Forex pair (e.g. 'EUR/USD', 'USD/JPY', 'XAU/USD').

    Returns:
        1 pip in price terms (0.0001, 0.01 for JPY pairs, or the metal's pip).
    """
    metal = _metal_base(symbol)
    if metal is not None:
        return _METAL_SPECS[metal][0]
    quote = symbol.split("/")[1] if "/" in symbol else symbol[3:6]
    return 0.01 if quote.upper() == "JPY" else 0.0001


def _lot_units(symbol: str, default: float = STANDARD_LOT) -> float:
    """Units in one standard lot: 100,000 for FX, 100 oz for gold, etc."""
    metal = _metal_base(symbol)
    return _METAL_SPECS[metal][1] if metal is not None else default


class ForexEngine(BaseEngine):
    """Forex engine for spot / CFD pairs.

    Config keys:
      - leverage: default 100.0 (100:1)
      - spread_pips_override: override spread for all pairs
      - lot_size: default 100000 (standard lot)
      - swap_enabled: default True
      - slippage_pips: additional slippage beyond spread, default 0.3
    """

    def __init__(self, config: dict):
        config = {**config, "leverage": config.get("leverage", 100.0)}
        super().__init__(config)
        self.spread_override = config.get("spread_pips_override")
        self.lot_size: float = config.get("lot_size", STANDARD_LOT)
        self.swap_enabled: bool = config.get("swap_enabled", True)
        self.slippage_pips: float = config.get("slippage_pips", 0.3)
        self._last_swap_dates: dict = {}  # per-symbol swap tracking

    def can_execute(self, symbol: str, direction: int, bar: pd.Series) -> bool:
        """Forex: 24x5, no restrictions."""
        return True

    def round_size(self, raw_size: float, price: float) -> float:
        """Round to micro-lot granularity (0.01 lots).

        Position size is in units of the base asset (not lots) for PnL
        compatibility. A micro lot is 1/100th of a standard lot, which is 1,000
        units for FX and 1 troy ounce for gold — rounding gold to the FX
        granularity would silently discard every position under 1,000 oz.
        """
        micro = _lot_units(_normalize_symbol(self._active_symbol), self.lot_size) / 100.0
        if micro <= 0:
            return max(raw_size, 0.0)
        return max(int(raw_size / micro) * micro, 0.0)

    def calc_commission(self, size: float, price: float, _direction: int, is_open: bool) -> float:
        """Forex: spread is the cost, embedded in slippage. No explicit commission.

        Some ECN brokers charge per-lot commission; for simplicity, zero here.
        The cost is captured via apply_slippage (half-spread applied to execution).
        ``_direction`` is unused — reserved for future ECN per-lot fee
        modelling (asymmetric long/short funding).
        """
        return 0.0

    def apply_slippage(self, price: float, direction: int) -> float:
        """Apply half-spread + slippage using _active_symbol for correct pip/spread."""
        return self.apply_slippage_for_symbol(self._active_symbol, price, direction)

    def apply_slippage_for_symbol(self, symbol: str, price: float, direction: int) -> float:
        """Symbol-aware slippage with correct spread.

        Args:
            symbol: Forex pair.
            price: Mid price.
            direction: 1 (buy) or -1 (sell).

        Returns:
            Slipped price.
        """
        pair = _normalize_symbol(symbol)
        pip = _pip_value(pair)

        if self.spread_override is not None:
            spread_pips = self.spread_override
        else:
            spread_pips = _SPREAD_PIPS.get(pair, _DEFAULT_SPREAD_PIPS)

        total_pips = (spread_pips / 2) + self.slippage_pips
        return price + direction * total_pips * pip

    def on_bar(self, symbol: str, bar: pd.Series, timestamp: pd.Timestamp) -> None:
        """Apply daily swap/rollover at end of trading day."""
        if not self.swap_enabled:
            return
        swap = calc_forex_swap(
            symbol, timestamp, self.positions,
            _lot_units(_normalize_symbol(symbol), self.lot_size), self._last_swap_dates,
        )
        self.capital += swap

    def get_contract_multiplier(self, symbol: str) -> float:
        """Forex: multiplier is 1.0 (size is in currency units)."""
        return 1.0
