"""Regression: Shoonya SDK must tolerate empty-string numeric fields.

The Shoonya API frequently returns ``""`` for unset numeric fields (e.g.
``netavgprc``, ``lp``, ``prc``).  A bare ``float(item.get("field", 0))`` crashes
with ``ValueError`` because the ``.get`` default only fires on a *missing* key,
not on an empty-string *value*.  The SDK now routes every numeric coercion
through ``_to_float`` / ``_to_int`` which treat ``""`` and ``None`` as 0.
"""

from __future__ import annotations

from src.markets.trading.connectors.shoonya import sdk as sh


# --------------------------------------------------------------------------- #
# _to_float / _to_int helpers
# --------------------------------------------------------------------------- #


def test_to_float_handles_empty_string() -> None:
    """Empty string — the actual Shoonya sentinel for 'no value' — must not raise."""
    assert sh._to_float("") == 0.0
    assert sh._to_float(None) == 0.0
    assert sh._to_float("123.45") == 123.45
    assert sh._to_float(123.45) == 123.45
    assert sh._to_float("abc") == 0.0


def test_to_int_handles_empty_string() -> None:
    assert sh._to_int("") == 0
    assert sh._to_int(None) == 0
    assert sh._to_int("100") == 100
    assert sh._to_int("100.00") == 100
    assert sh._to_int(100) == 100
    assert sh._to_int("abc") == 0


# --------------------------------------------------------------------------- #
# Integration: the four parse functions that used to crash on ""
# --------------------------------------------------------------------------- #


class _FakeShoonyaApi:
    """Minimal stub returning canned payloads with empty-string numerics."""

    def __init__(self, *, positions=None, quote=None, bars=None, orders=None):
        self._positions = positions
        self._quote = quote
        self._bars = bars
        self._orders = orders

    def get_positions(self):
        return self._positions

    def get_quotes(self, exchange, token):
        return self._quote

    def get_daily_price_series(self, **kw):
        return self._bars

    def get_time_price_series(self, **kw):
        return self._bars

    def get_order_book(self):
        return self._orders


def _patch_login(monkeypatch, fake):
    monkeypatch.setattr(sh, "_login", lambda cfg: fake)


def test_get_positions_tolerates_empty_string_numerics(monkeypatch) -> None:
    """Shoonya returns ``""`` for unset position fields — must not crash."""
    fake = _FakeShoonyaApi(positions=[{
        "tsym": "RELIANCE",
        "exch": "NSE",
        "prd": "CNC",
        "netqty": "",
        "netavgprc": "",
        "lp": "",
        "urmtom": "",
        "rpnl": "",
        "daybuyqty": "",
        "daysellqty": "",
    }])
    _patch_login(monkeypatch, fake)
    cfg = sh.ShoonyaConfig(profile="paper")
    result = sh.get_positions(cfg)
    assert result["status"] == "ok"
    row = result["positions"][0]
    assert row["quantity"] == 0
    assert row["average_cost"] == 0.0
    assert row["ltp"] == 0.0
    assert row["unrealized_pnl"] == 0.0
    assert row["realized_pnl"] == 0.0
    assert row["day_buy_qty"] == 0
    assert row["day_sell_qty"] == 0


def test_get_quote_tolerates_empty_string_numerics(monkeypatch) -> None:
    """Shoonya returns ``""`` for unset quote fields — must not crash."""
    fake = _FakeShoonyaApi(quote={
        "lp": "", "o": "", "h": "", "l": "", "c": "",
        "v": "", "bp1": "", "sp1": "",
    })
    _patch_login(monkeypatch, fake)
    cfg = sh.ShoonyaConfig(profile="paper")
    result = sh.get_quote("RELIANCE", config=cfg)
    assert result["status"] == "ok"
    q = result["quote"]
    assert q["ltp"] == 0.0
    assert q["open"] == 0.0
    assert q["high"] == 0.0
    assert q["low"] == 0.0
    assert q["close"] == 0.0
    assert q["volume"] == 0
    assert q["bid"] == 0.0
    assert q["ask"] == 0.0


def test_get_historical_bars_tolerates_empty_string_numerics(monkeypatch) -> None:
    """Shoonya returns ``""`` for unset OHLCV fields — must not crash."""
    fake = _FakeShoonyaApi(bars=[{
        "time": "2026-01-01",
        "into": "", "inth": "", "intl": "", "intc": "", "intv": "",
    }])
    _patch_login(monkeypatch, fake)
    cfg = sh.ShoonyaConfig(profile="paper")
    result = sh.get_historical_bars("RELIANCE", config=cfg, period="1d")
    assert result["status"] == "ok"
    bar = result["bars"][0]
    assert bar["open"] == 0.0
    assert bar["high"] == 0.0
    assert bar["low"] == 0.0
    assert bar["close"] == 0.0
    assert bar["volume"] == 0


def test_order_to_dict_tolerates_empty_string_numerics() -> None:
    """``_order_to_dict`` must not crash on empty-string qty/prc fields."""
    row = sh._order_to_dict({
        "norenordno": "ORD1",
        "tsym": "RELIANCE",
        "exch": "NSE",
        "trantype": "B",
        "prctyp": "LMT",
        "qty": "",
        "fillshares": "",
        "prc": "",
        "status": "PENDING",
        "prd": "CNC",
    })
    assert row["quantity"] == 0
    assert row["filled_qty"] == 0
    assert row["price"] == 0.0


def test_positions_mixed_empty_and_valid_values(monkeypatch) -> None:
    """A mix of empty and valid fields must parse correctly."""
    fake = _FakeShoonyaApi(positions=[
        {"tsym": "A", "exch": "NSE", "prd": "CNC", "netqty": "100", "netavgprc": "200.5",
         "lp": "210", "urmtom": "950", "rpnl": "", "daybuyqty": "100", "daysellqty": ""},
        {"tsym": "B", "exch": "NSE", "prd": "CNC", "netqty": "", "netavgprc": "",
         "lp": "", "urmtom": "", "rpnl": "500", "daybuyqty": "", "daysellqty": ""},
    ])
    _patch_login(monkeypatch, fake)
    cfg = sh.ShoonyaConfig(profile="paper")
    result = sh.get_positions(cfg)
    assert result["status"] == "ok"
    r0, r1 = result["positions"]
    assert r0["quantity"] == 100 and r0["average_cost"] == 200.5
    assert r0["ltp"] == 210.0 and r0["unrealized_pnl"] == 950.0
    assert r0["realized_pnl"] == 0.0  # empty string → 0
    assert r1["quantity"] == 0 and r1["average_cost"] == 0.0
    assert r1["realized_pnl"] == 500.0  # valid value preserved
