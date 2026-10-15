"""Tests for the Alpaca position mapping's quantity sign.

Alpaca reports ``qty`` as an absolute magnitude and carries direction in
``side`` (``"long"``/``"short"``). The mandate gate reads position quantity as
signed (its own fixtures use negative quantities for shorts), so an unsigned
short would book as positive exposure and every sell would move the computed
exposure toward zero. The connector now negates qty for short positions.
"""

from __future__ import annotations

from enum import Enum
from types import SimpleNamespace

import pytest

from src.markets.live.enforcement import _position_signed_market_value
from src.markets.trading.connectors.alpaca.sdk import _position_to_dict

pytestmark = pytest.mark.unit


def test_short_position_gets_negative_quantity() -> None:
    row = _position_to_dict({"symbol": "AAPL", "side": "short", "qty": "400", "market_value": "-50000"})
    assert row["quantity"] == -400.0
    assert row["exact_quantity"] == "-400"
    assert row["side"] == "short"


def test_long_position_quantity_passes_through() -> None:
    row = _position_to_dict({"symbol": "AAPL", "side": "long", "qty": "400", "market_value": "50000"})
    assert row["quantity"] == "400"
    assert row["exact_quantity"] == "400"


def test_fractional_position_keeps_an_exact_recovery_quantity() -> None:
    row = _position_to_dict(
        {"symbol": "AAPL", "side": "short", "qty": "0.123456789123456789"}
    )
    assert row["exact_quantity"] == "-0.123456789123456789"


def test_short_position_without_qty_stays_none() -> None:
    row = _position_to_dict({"symbol": "AAPL", "side": "short", "qty": None})
    assert row["quantity"] is None


def test_object_positions_get_the_same_sign() -> None:
    item = SimpleNamespace(symbol="AAPL", side="short", qty="400", market_value="-50000")
    row = _position_to_dict(item)
    assert row["quantity"] == -400.0


def test_gate_reads_alpaca_short_as_negative_exposure() -> None:
    row = _position_to_dict({"symbol": "AAPL", "side": "short", "qty": "400", "market_value": "-50000"})
    assert _position_signed_market_value(row) == -50000.0


def test_sdk_enum_side_is_normalized_and_signed() -> None:
    # The direct-SDK path yields alpaca-py Position objects whose side is a
    # (str, Enum) member; str() of such a member is "PositionSide.SHORT".
    class PositionSide(str, Enum):
        SHORT = "short"

    item = SimpleNamespace(symbol="AAPL", side=PositionSide.SHORT, qty="400", market_value="-50000")
    row = _position_to_dict(item)
    assert row["side"] == "short"
    assert row["quantity"] == -400.0
    assert _position_signed_market_value(row) == -50000.0
