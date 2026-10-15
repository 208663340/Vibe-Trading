"""Tests for the verified IBKR public MCP generic read mapping."""

from __future__ import annotations

import pytest

from src.markets.trading.connectors.ibkr.mcp import (
    normalize_result,
    remote_arguments,
    remote_tool_name,
)

pytestmark = pytest.mark.unit


def test_ibkr_remote_tools_map_only_verified_reads() -> None:
    assert remote_tool_name("account") == "get_account_summary"
    assert remote_tool_name("positions") == "get_account_positions"
    assert remote_tool_name("orders") is None
    assert remote_arguments("account", {"account_number": "ignored"}) == {}


def test_ibkr_account_summary_normalizes_for_portfolio_totals() -> None:
    result = normalize_result(
        "account",
        {
            "status": "ok",
            "structured_content": {
                "currency": "USD",
                "net_liquidation": 123.45,
                "total_cash_value": 12.34,
            },
        },
    )

    assert result["summary"] == [
        {"tag": "NetLiquidation", "value": 123.45, "currency": "USD"},
        {"tag": "TotalCashValue", "value": 12.34, "currency": "USD"},
    ]


def test_ibkr_positions_normalize_for_shared_position_reader() -> None:
    result = normalize_result(
        "positions",
        {
            "status": "ok",
            "structured_content": {
                "positions": [
                    {
                        "contract_id": 1,
                        "contract_description": "EXAMPLE",
                        "position": 2.0,
                        "market_price": 10.0,
                        "market_value": 20.0,
                        "currency": "USD",
                        "average_price": 8.0,
                        "unrealized_pnl": 4.0,
                        "asset_class": "STK",
                    }
                ]
            },
        },
    )

    assert result["positions"] == [
        {
            "contract_id": 1,
            "symbol": "EXAMPLE",
            "position": 2.0,
            "market_price": 10.0,
            "market_value": 20.0,
            "currency": "USD",
            "avg_cost": 8.0,
            "unrealized_pnl": 4.0,
            "sec_type": "STK",
        }
    ]
