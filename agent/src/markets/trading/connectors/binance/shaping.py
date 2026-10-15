"""Pure symbol and response shaping helpers for the Binance connector."""

from __future__ import annotations

from typing import Any, Mapping


_QUOTE_ASSETS = ("USDT", "USDC", "BUSD", "TUSD", "FDUSD", "BTC", "ETH", "BNB")


def normalize_symbol(symbol: str) -> str:
    """Normalize a symbol to ccxt unified ``BASE/QUOTE`` format."""
    clean = (symbol or "").strip().upper().replace("-", "/")
    if "/" in clean:
        return clean
    for quote in _QUOTE_ASSETS:
        if clean.endswith(quote) and len(clean) > len(quote):
            return f"{clean[: -len(quote)]}/{quote}"
    return clean


def as_iter(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def obj_get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def nonzero_balances(balance: Any) -> list[dict[str, Any]]:
    """Extract non-zero per-asset balances from a ccxt balance response."""
    rows: list[dict[str, Any]] = []
    if not isinstance(balance, Mapping):
        return rows
    skip = {"info", "timestamp", "datetime", "free", "used", "total"}
    for asset, detail in balance.items():
        if asset in skip or not isinstance(detail, Mapping):
            continue
        total = to_float(detail.get("total"))
        if not total:
            continue
        rows.append(
            {
                "asset": asset,
                "free": to_float(detail.get("free")),
                "used": to_float(detail.get("used")),
                "total": total,
            }
        )
    return rows


def order_to_dict(item: Any) -> dict[str, Any]:
    return {
        "order_id": str(obj_get(item, "id", "")),
        "symbol": obj_get(item, "symbol"),
        "side": str(obj_get(item, "side", "")),
        "order_type": str(obj_get(item, "type", "")),
        "price": obj_get(item, "price"),
        "quantity": obj_get(item, "amount"),
        "filled": obj_get(item, "filled"),
        "remaining": obj_get(item, "remaining"),
        "status": str(obj_get(item, "status", "")),
        "time": str(obj_get(item, "timestamp", "")),
    }


def trade_to_dict(item: Any) -> dict[str, Any]:
    return {
        "trade_id": str(obj_get(item, "id", "")),
        "order_id": str(obj_get(item, "order", "")),
        "symbol": obj_get(item, "symbol"),
        "side": str(obj_get(item, "side", "")),
        "price": obj_get(item, "price"),
        "quantity": obj_get(item, "amount"),
        "cost": obj_get(item, "cost"),
        "time": str(obj_get(item, "timestamp", "")),
    }


def ohlcv_to_dict(item: Any) -> dict[str, Any]:
    """Shape a ccxt OHLCV row into a named dictionary."""
    row = list(item) if isinstance(item, (list, tuple)) else []
    row += [None] * (6 - len(row))
    return {
        "time": str(row[0] if row[0] is not None else ""),
        "open": row[1],
        "high": row[2],
        "low": row[3],
        "close": row[4],
        "volume": row[5],
    }
