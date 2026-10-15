"""Built-in MetaTrader 5 connector profiles.

"Paper" is the broker's DEMO MT5 account (e.g. an Exness demo) — MT5 has no
separate sandbox API. Unlike key-scoped connectors the discriminator is hard:
every session re-reads ``account_info().trade_mode`` from the terminal and
rejects a paper profile attached to a real-money account (and vice versa;
contest accounts are rejected everywhere). The order-placing profiles add
``orders.place``; the live one is gated behind a mandate + kill switch.
Requires Windows, the ``MetaTrader5`` extra, and a running logged-in terminal.
"""

from __future__ import annotations

from src.markets.trading.types import READ_CAPABILITIES, TradingProfile

MT5_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="mt5-paper-sdk",
        connector="mt5",
        label="MetaTrader 5 Demo · local terminal",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "paper"},
        notes=(
            "Reads a DEMO MT5 account through the local terminal (Windows-only "
            "MetaTrader5 package). The terminal's trade_mode and login are "
            "re-verified on every call; a real-money account is hard-rejected."
        ),
    ),
    TradingProfile(
        id="mt5-live-sdk-readonly",
        connector="mt5",
        label="MetaTrader 5 Live · Read-Only",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes=(
            "Reads a REAL MT5 account only. Order placement is not exposed in "
            "this profile; a demo account is hard-rejected (identity guard)."
        ),
    ),
    TradingProfile(
        id="mt5-paper-trade",
        connector="mt5",
        label="MetaTrader 5 Demo · Trading",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place",),
        readonly=False,
        config={"profile": "paper"},
        notes=(
            "Places orders on a DEMO MT5 account (e.g. Exness demo). Connector "
            "size guards (max_order_volume / max_order_notional_usd in mt5.json) "
            "apply even on demo. Note: MT5 hedging accounts OPEN a hedge on an "
            "opposite-side order; close positions by ticket via cancel."
        ),
    ),
    TradingProfile(
        id="mt5-live-trade",
        connector="mt5",
        label="MetaTrader 5 Live · Trading",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place.requires_mandate",),
        readonly=False,
        config={"profile": "live"},
        notes=(
            "Places orders on a REAL MT5 account. Live order placement is gated "
            "behind a user-defined mandate (forex/cfd instrument allowances, "
            "lot-aware USD notional caps) and the kill switch, plus the "
            "connector's own size guards."
        ),
    ),
)
