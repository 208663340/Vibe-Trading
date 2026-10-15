"""Built-in eToro connector profiles.

Layer A ships read-only paper and live profiles; the trade profiles add order
placement, position management, and copy-trading workflows. Demo and real share
one API key pair — the profile selects ``/demo`` vs production API paths.
"""

from __future__ import annotations

from src.markets.trading.types import READ_CAPABILITIES, TradingProfile

ETORO_PAPER_TRADE_CAPABILITIES = READ_CAPABILITIES + (
    "orders.place",
    "orders.cancel",
    "positions.close",
    "orders.cancel_close",
    "positions.edit",
)

ETORO_EXTENDED_CAPABILITIES = ETORO_PAPER_TRADE_CAPABILITIES + (
    "copy.precheck",
    "copy.start",
    "copy.poll",
    "copy.close",
)

ETORO_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="etoro-paper-sdk",
        connector="etoro",
        label="eToro Demo · Public API Read-Only",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "paper"},
        notes="Reads a demo account via eToro Public API demo paths.",
    ),
    TradingProfile(
        id="etoro-live-sdk-readonly",
        connector="etoro",
        label="eToro Real · Public API Read-Only",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes="Reads a real account only (production API paths). Order placement is not exposed in this profile.",
    ),
    TradingProfile(
        id="etoro-paper-trade",
        connector="etoro",
        label="eToro Demo · Public API Trade",
        environment="paper",
        transport="broker_sdk",
        capabilities=ETORO_PAPER_TRADE_CAPABILITIES,
        readonly=False,
        config={"profile": "paper"},
        notes=(
            "Demo trading via eToro Public API demo paths. "
            "Copy trading is not available on demo accounts — use etoro-live-trade."
        ),
    ),
    TradingProfile(
        id="etoro-live-trade",
        connector="etoro",
        label="eToro Real · Public API Trade",
        environment="live",
        transport="broker_sdk",
        capabilities=tuple(
            cap if cap != "orders.place" else "orders.place.requires_mandate"
            for cap in ETORO_EXTENDED_CAPABILITIES
        ),
        readonly=False,
        config={"profile": "live"},
        notes=(
            "Real trading and copy workflows via production API paths. "
            "Risk-increasing actions require an authorized mandate; the caller enforces it."
        ),
    ),
)
