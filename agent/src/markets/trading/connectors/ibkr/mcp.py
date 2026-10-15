"""IBKR official MCP mapping for generic read-only trading operations."""

from __future__ import annotations

from typing import Any

_REMOTE_TOOL_NAMES = {
    "account": "get_account_summary",
    "positions": "get_account_positions",
}

_ACCOUNT_TAGS = {
    "net_liquidation": "NetLiquidation",
    "equity_with_loan_value": "EquityWithLoanValue",
    "buying_power": "BuyingPower",
    "gross_position_value": "GrossPositionValue",
    "total_cash_value": "TotalCashValue",
    "available_funds": "AvailableFunds",
    "initial_margin": "InitMarginReq",
    "maintenance_margin": "MaintMarginReq",
    "excess_liquidity": "ExcessLiquidity",
    "dividends": "AccruedCash",
    "leverage": "Leverage",
}


def remote_tool_name(operation: str) -> str | None:
    """Return the verified IBKR public MCP tool for a generic operation."""
    return _REMOTE_TOOL_NAMES.get(operation)


def remote_arguments(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Return IBKR's argument-free account/position request payload."""
    if operation in _REMOTE_TOOL_NAMES:
        return {}
    return dict(arguments)


def normalize_result(operation: str, result: dict[str, Any]) -> dict[str, Any]:
    """Translate IBKR's structured MCP result into the shared broker shapes."""
    normalized = dict(result)
    structured = result.get("structured_content")
    if not isinstance(structured, dict):
        return normalized

    if operation == "account":
        currency = str(structured.get("currency") or "USD").upper()
        normalized["summary"] = [
            {"tag": tag, "value": structured.get(key), "currency": currency}
            for key, tag in _ACCOUNT_TAGS.items()
            if structured.get(key) is not None
        ]
        return normalized

    if operation == "positions":
        rows = structured.get("positions")
        if not isinstance(rows, list):
            return normalized
        normalized["positions"] = [
            {
                "contract_id": row.get("contract_id"),
                "symbol": row.get("contract_description"),
                "position": row.get("position"),
                "market_price": row.get("market_price"),
                "market_value": row.get("market_value"),
                "currency": row.get("currency"),
                "avg_cost": row.get("average_price"),
                "unrealized_pnl": row.get("unrealized_pnl"),
                "sec_type": row.get("asset_class"),
            }
            for row in rows
            if isinstance(row, dict)
        ]
    return normalized
