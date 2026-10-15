"""Tests for CompositeEngine fallback when an unknown symbol is encountered."""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.engines.composite import CompositeEngine
from src.backtest.models import Position


class TestCompositeEngineFallback:
    """Verify that CompositeEngine does not crash on unknown symbols."""

    def test_unknown_symbol_round_size(self):
        """round_size for a symbol not in the original codes should not crash."""
        config = {"initial_cash": 100_000}
        engine = CompositeEngine(config, ["BTC-USDT", "ETH-USDT"])
        result = engine.round_size(1.0, 100.0)
        assert isinstance(result, float)
        assert result >= 0.0

    def test_unknown_symbol_calc_commission(self):
        """calc_commission for a symbol not in the original codes should not crash."""
        config = {"initial_cash": 100_000}
        engine = CompositeEngine(config, ["BTC-USDT"])
        fee = engine.calc_commission(1.0, 100.0, 1, True)
        assert isinstance(fee, float)
        assert fee >= 0.0

    def test_unknown_symbol_leverage(self):
        """_leverage_for_symbol for a symbol not in the original codes should not crash."""
        config = {"initial_cash": 100_000}
        engine = CompositeEngine(config, ["BTC-USDT"])
        lev = engine._leverage_for_symbol("UNKNOWN-SYMBOL")
        assert isinstance(lev, float)
        assert lev > 0

    def test_known_symbols_still_use_correct_engine(self):
        """Known symbols should still route to their dedicated sub-engine."""
        config = {"initial_cash": 100_000, "maker_rate": 0.001}
        engine = CompositeEngine(config, ["BTC-USDT", "ETH-USDT"])
        # BTC-USDT should use the crypto sub-engine (maker_rate=0.001 from config)
        fee = engine.calc_commission(1.0, 50000.0, 1, False)
        # Crypto maker rate is 0.0002 by default, but config overrides to 0.001
        assert fee == pytest.approx(1.0 * 50000.0 * 0.001)

    def test_empty_rule_engines_raises(self):
        """If somehow _rule_engines is empty, should raise ValueError."""
        config = {"initial_cash": 100_000}
        engine = CompositeEngine(config, ["BTC-USDT"])
        engine._rule_engines = {}
        with pytest.raises(ValueError, match="No sub-engines available"):
            engine._rule_for("UNKNOWN-SYMBOL")


class TestSharedSubEngineActiveSymbol:
    """Two symbols on the same sub-engine must not leak each other's state.

    round_size/calc_commission dispatch through one shared sub-engine
    instance per market. ForexEngine.round_size and
    ChinaFuturesEngine.calc_commission both key off the sub-engine's OWN
    _active_symbol, so it must be refreshed on every call, not just when
    apply_slippage happens to run first.
    """

    def test_round_size_reflects_current_symbol_not_last_synced_one(self):
        config = {"initial_cash": 100_000}
        engine = CompositeEngine(config, ["EUR/USD", "XAU/USD"])

        # apply_slippage is what used to sync the sub-engine's own
        # _active_symbol; call it for one symbol, then round_size for a
        # different one with no apply_slippage call in between (the exact
        # sequence the capital-fit rescale loop uses).
        engine._active_symbol = "EUR/USD"
        engine.apply_slippage(1.1000, 1)

        engine._active_symbol = "XAU/USD"
        size = engine.round_size(50.0, 2000.0)
        # Gold's micro lot is 1 oz; FX's is 1000 units. Stale FX state
        # rounds 50 oz down to zero instead of keeping it.
        assert size == pytest.approx(50.0)

    def test_calc_commission_reflects_current_symbol_not_last_synced_one(self):
        config = {"initial_cash": 10_000_000}
        engine = CompositeEngine(config, ["IF2406.CFFEX", "au2412.SHFE"])

        engine._active_symbol = "IF2406.CFFEX"
        engine.apply_slippage(4000.0, 1)

        engine._active_symbol = "au2412.SHFE"
        fee = engine.calc_commission(2.0, 500.0, 1, True)
        # au (gold) is a flat RMB10/lot fee; IF's stale rate-based formula
        # would price it at 2 * 500 * 300 * 0.000023 = 6.9 instead.
        assert fee == pytest.approx(2.0 * 10.0)

    def test_forex_swap_uses_metal_lot_size_not_standard_lot(self):
        """CompositeEngine.on_bar must size swap lots the way ForexEngine
        does — metal-aware — not with the raw 100,000-unit standard lot."""
        config = {"initial_cash": 100_000}
        engine = CompositeEngine(config, ["EUR/USD", "XAU/USD"])
        engine.positions["XAU/USD"] = Position(
            symbol="XAU/USD",
            direction=1,
            entry_price=2000.0,
            entry_time=pd.Timestamp("2025-06-10"),
            size=100.0,
        )
        initial_capital = engine.capital
        engine.on_bar(
            "XAU/USD", pd.Series({"close": 2000.0}), pd.Timestamp("2025-06-10 17:00")
        )
        # 100 oz is exactly 1 standard gold lot (100 oz/lot); the standard
        # FX lot (100,000 units) would understate this to 0.001 lots.
        assert engine.capital == pytest.approx(initial_capital - 1.0, abs=0.01)
