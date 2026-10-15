"""Read adapters and generic broker_sdk entry points for eToro."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.markets.trading.connectors.etoro.client import (
    BASE_URL,
    EtoroAPIError,
    EtoroConfig,
    EtoroConfigError,
    MARKET_DATA_RATES_PATH,
    PAPER_GUARD,
    aggregate_portfolio_path,
    build_config,
    credential_source,
    info_root,
    load_config,
    make_client,
    public_config,
    save_config,
    trade_history_root,
    _missing_credential_code,
    _missing_fields,
)
from src.markets.trading.connectors.etoro.copy_trading import (
    copy_close,
    copy_poll,
    copy_precheck,
    copy_start_or_adjust,
)
from src.markets.trading.connectors.etoro.instruments import (
    get_instrument_metadata,
    get_instrument_types,
    list_instruments_by_type,
    resolve_instrument_id,
    search_instruments,
)
from src.markets.trading.connectors.etoro.trading import (
    cancel_close_order,
    cancel_order as cancel_open_order,
    close_position,
    edit_position_stops,
    place_order as place_open_order,
)

__all__ = [
    "build_config",
    "load_config",
    "save_config",
    "check_status",
    "get_user_profile",
    "get_account_snapshot",
    "get_positions",
    "get_open_orders",
    "get_quote",
    "get_historical_bars",
    "place_order",
    "cancel_order",
    "close_position",
    "cancel_close_order",
    "edit_position_stops",
    "copy_precheck",
    "copy_start_or_adjust",
    "copy_poll",
    "copy_close",
    "search_instruments",
    "list_instruments_by_type",
    "get_instrument_types",
    "get_instrument_metadata",
    "EtoroConfig",
    "EtoroConfigError",
    "EtoroAPIError",
]


def _client(cfg: EtoroConfig):
    return make_client(cfg)


def _base_payload(cfg: EtoroConfig) -> dict[str, Any]:
    return {
        "profile": cfg.profile,
        "environment": cfg.environment,
        "paper_guard": PAPER_GUARD,
    }


def check_status(config: EtoroConfig | None = None) -> dict[str, Any]:
    """Check read-only readiness with stable, redaction-safe diagnostics."""
    cfg = config or load_config()
    configured = not _missing_fields(cfg)
    report: dict[str, Any] = {
        "status": "ok",
        "configured": configured,
        "credential_source": credential_source(),
        "connection_state": "connected",
        "error_code": None,
        "error": None,
        "config": public_config(cfg),
        "sdk": {"package": "requests", "installed": True},
        "paper_guard": PAPER_GUARD,
        "base_url": BASE_URL,
    }
    if not configured:
        code = _missing_credential_code(cfg)
        fields = ", ".join(_missing_fields(cfg))
        message = (
            f"eToro credentials are partial; missing fields: {fields}."
            if code == "credentials_partial"
            else f"eToro connector not configured: missing {fields}."
        )
        return _status_error(report, code, message)
    try:
        me = get_user_profile(cfg)
        portfolio = get_account_snapshot(cfg)
    except (EtoroConfigError, EtoroAPIError) as exc:
        code = _connection_error_code(exc)
        return _status_error(report, code, _connection_error_message(code, str(exc)))
    except Exception as exc:  # noqa: BLE001
        return _status_error(report, "broker_error", f"eToro connector check failed: {exc}")
    report["account"] = {
        "profile": cfg.profile,
        "portfolio_status": portfolio.get("status"),
        "scopes": me.get("scopes"),
        "real_cid": me.get("realCid") or me.get("realCID"),
        "demo_cid": me.get("demoCid") or me.get("demoCID"),
    }
    account = portfolio.get("account") if isinstance(portfolio.get("account"), dict) else {}
    pnl = account.get("pnl") if isinstance(account.get("pnl"), dict) else {}
    if pnl.get("account_current_pnl") is not None:
        report["account"]["account_current_pnl"] = pnl.get("account_current_pnl")
    report["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return report


def _status_error(report: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    if code in ("credentials_missing", "credentials_partial"):
        report["configured"] = False
    report.update(
        status="error",
        connection_state=(
            "not_configured" if code in ("credentials_missing", "credentials_partial") else "error"
        ),
        error_code=code,
        error=message,
    )
    return report


def _connection_error_code(exc: Exception) -> str:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "network_unreachable"
    if isinstance(exc, EtoroAPIError):
        text = str(exc).lower()
        if any(token in text for token in ("401", "403", "auth", "unauthorized", "forbidden")):
            return "authentication_failed"
        if "network error" in text:
            return "network_unreachable"
    return "broker_error"


def _connection_error_message(code: str, detail: str) -> str:
    if code == "authentication_failed":
        return "eToro authentication failed."
    if code == "network_unreachable":
        return "eToro Public API is unreachable."
    return detail or "eToro broker request failed."


def get_user_profile(config: EtoroConfig | None = None) -> dict[str, Any]:
    """Return authenticated user profile and granted API scopes (`GET /api/v1/me`)."""
    cfg = config or load_config()
    payload = _client(cfg).request("GET", "/api/v1/me", allow_retry=True)
    if isinstance(payload, dict):
        return payload
    return {"raw": payload}


def get_account_snapshot(config: EtoroConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    client = _client(cfg)
    portfolio = client.request("GET", f"{info_root(cfg)}/portfolio", allow_retry=True)
    account: dict[str, Any] = {"portfolio": portfolio}
    try:
        aggregated = client.request("GET", aggregate_portfolio_path(cfg), allow_retry=True)
        account["aggregated_portfolio"] = aggregated
        account["pnl"] = _pnl_from_aggregate_portfolio(aggregated)
    except EtoroAPIError as exc:
        account["pnl_error"] = str(exc)
    return {
        "status": "ok",
        **_base_payload(cfg),
        "account": account,
    }


def get_positions(config: EtoroConfig | None = None) -> dict[str, Any]:
    cfg = config or load_config()
    payload = _client(cfg).request("GET", f"{info_root(cfg)}/portfolio", allow_retry=True)
    positions = _extract_positions(payload)
    _enrich_positions_with_metadata(cfg, positions)
    return {"status": "ok", **_base_payload(cfg), "positions": positions}


def get_open_orders(config: EtoroConfig | None = None, *, include_executions: bool = False) -> dict[str, Any]:
    cfg = config or load_config()
    portfolio_payload = _client(cfg).request("GET", f"{info_root(cfg)}/portfolio", allow_retry=True)
    orders = _extract_portfolio_orders(portfolio_payload)
    result: dict[str, Any] = {"status": "ok", **_base_payload(cfg), "orders": orders}
    if include_executions:
        try:
            history = _client(cfg).request("GET", f"{trade_history_root(cfg)}/history", allow_retry=True)
            result["history"] = history
        except EtoroAPIError as exc:
            result["history_error"] = str(exc)
    return result


def get_quote(symbol: str, *, config: EtoroConfig | None = None, **_: Any) -> dict[str, Any]:
    """Fetch a quote via the flat market-data rates route (all profiles)."""
    cfg = config or load_config()
    instrument_id = resolve_instrument_id(symbol, cfg)
    payload = _client(cfg).request(
        "GET",
        MARKET_DATA_RATES_PATH,
        params={"instrumentIds": instrument_id},
        allow_retry=True,
    )
    quote = _first_rate(payload, instrument_id)
    return {
        "status": "ok",
        **_base_payload(cfg),
        "symbol": symbol,
        "instrument_id": instrument_id,
        "quote": quote,
    }


def get_historical_bars(
    symbol: str,
    *,
    config: EtoroConfig | None = None,
    period: str = "1d",
    limit: int = 90,
    **_: Any,
) -> dict[str, Any]:
    cfg = config or load_config()
    instrument_id = resolve_instrument_id(symbol, cfg)
    interval = _map_interval(period)
    count = max(1, min(int(limit), 1000))
    path = f"/api/v1/market-data/instruments/{instrument_id}/history/candles/desc/{interval}/{count}"
    payload = _client(cfg).request("GET", path, allow_retry=True)
    bars = _normalize_candles(payload)
    return {
        "status": "ok",
        **_base_payload(cfg),
        "symbol": symbol,
        "instrument_id": instrument_id,
        "period": period,
        "bars": bars,
    }


def place_order(
    config: EtoroConfig | None = None,
    *,
    symbol: str,
    side: str,
    quantity: float | None = None,
    notional: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
    request_id: str | None = None,
) -> dict[str, Any]:
    return place_open_order(
        config,
        symbol=symbol,
        side=side,
        quantity=quantity,
        notional=notional,
        order_type=order_type,
        limit_price=limit_price,
        time_in_force=time_in_force,
        request_id=request_id,
    )


def cancel_order(
    config: EtoroConfig | None = None,
    order_id: str = "",
    *,
    symbol: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return cancel_open_order(config, order_id, symbol=symbol, request_id=request_id)


def _map_interval(period: str) -> str:
    token = str(period or "1d").strip().lower()
    mapping = {
        "1m": "OneMinute",
        "5m": "FiveMinutes",
        "15m": "FifteenMinutes",
        "30m": "ThirtyMinutes",
        "1h": "OneHour",
        "4h": "FourHours",
        "1d": "OneDay",
        "1w": "OneWeek",
    }
    if token in mapping:
        return mapping[token]
    raise EtoroAPIError(f"unsupported period {period!r}")


def _extract_positions(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("positions", "clientPortfolio", "portfolio", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [_position_row(item) for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = value.get("positions")
                if isinstance(nested, list):
                    return [_position_row(item) for item in nested if isinstance(item, dict)]
    if isinstance(payload, list):
        return [_position_row(item) for item in payload if isinstance(item, dict)]
    return []


def _position_row(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_id": item.get("positionID") or item.get("positionId") or item.get("id"),
        "instrument_id": item.get("instrumentID") or item.get("instrumentId"),
        "symbol": item.get("instrumentDisplayName") or item.get("symbol") or item.get("instrumentName"),
        "units": item.get("units") or item.get("amountInUnits"),
        "open_rate": item.get("openRate") or item.get("openPrice"),
        "pnl": item.get("profit") or item.get("pnl"),
        "is_buy": item.get("isBuy"),
        "raw": item,
    }


def _pnl_from_aggregate_portfolio(payload: Any) -> dict[str, Any]:
    """Normalize account-level PnL from ``GET …/aggregate-portfolio``."""
    if not isinstance(payload, dict):
        return {"source": "aggregate-portfolio", "raw": payload}
    totals = payload.get("accountTotals")
    if not isinstance(totals, dict):
        return {"source": "aggregate-portfolio", "raw": payload}
    return {
        "source": "aggregate-portfolio",
        "account_current_pnl": totals.get("accountCurrentPnl"),
        "account_total_value": totals.get("accountTotalValue"),
        "account_balance": totals.get("accountBalance"),
        "account_available_cash": totals.get("accountAvailableCash"),
        "account_frozen_cash": totals.get("accountFrozenCash"),
        "account_total_used_margin": totals.get("accountTotalUsedMargin"),
        "account_currency": payload.get("accountCurrency"),
        "timestamp": payload.get("timestamp"),
        "account_totals": totals,
    }


def _client_portfolio(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    client_portfolio = payload.get("clientPortfolio")
    if isinstance(client_portfolio, dict):
        return client_portfolio
    for key in ("portfolio", "data"):
        nested = payload.get(key)
        if isinstance(nested, dict) and any(
            isinstance(nested.get(field), list) for field in ("positions", "orders", "ordersForOpen")
        ):
            return nested
    return None


def _extract_portfolio_orders(payload: Any) -> list[dict[str, Any]]:
    client_portfolio = _client_portfolio(payload)
    if client_portfolio is None:
        return []
    order_keys = (
        "orders",
        "ordersForOpen",
        "ordersForClose",
        "ordersForCloseMultiple",
        "entryOrders",
        "exitOrders",
        "stockOrders",
        "pendingOrders",
    )
    seen_ids: set[Any] = set()
    rows: list[dict[str, Any]] = []
    for key in order_keys:
        items = client_portfolio.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            order_id = item.get("orderID") or item.get("orderId") or item.get("id")
            if order_id is not None and order_id in seen_ids:
                continue
            if order_id is not None:
                seen_ids.add(order_id)
            rows.append(
                {
                    "order_id": order_id,
                    "instrument_id": item.get("instrumentID") or item.get("instrumentId"),
                    "status": item.get("statusID") or item.get("status"),
                    "order_kind": key,
                    "raw": item,
                }
            )
    return rows


def _enrich_positions_with_metadata(cfg: EtoroConfig, positions: list[dict[str, Any]]) -> None:
    missing_ids: list[int] = []
    for row in positions:
        if row.get("symbol"):
            continue
        instrument_id = row.get("instrument_id")
        if instrument_id is None:
            continue
        try:
            missing_ids.append(int(instrument_id))
        except (TypeError, ValueError):
            continue
    if not missing_ids:
        return
    try:
        metadata = get_instrument_metadata(missing_ids, cfg)
    except EtoroAPIError:
        return
    labels = {
        int(item["instrument_id"]): item.get("symbol") or item.get("display_name")
        for item in metadata.get("instruments") or []
        if isinstance(item, dict) and item.get("instrument_id") is not None
    }
    for row in positions:
        instrument_id = row.get("instrument_id")
        if row.get("symbol") or instrument_id is None:
            continue
        try:
            label = labels.get(int(instrument_id))
        except (TypeError, ValueError):
            label = None
        if label:
            row["symbol"] = label


def _extract_orders(payload: Any) -> list[dict[str, Any]]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("items", "data", "orders", "pendingOrders"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    return [
        {
            "order_id": item.get("orderID") or item.get("orderId") or item.get("id"),
            "instrument_id": item.get("instrumentID") or item.get("instrumentId"),
            "status": item.get("statusID") or item.get("status"),
            "raw": item,
        }
        for item in items
        if isinstance(item, dict)
    ]


def _first_rate(payload: Any, instrument_id: int) -> dict[str, Any]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("items", "data", "rates"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("instrumentID") or item.get("instrumentId")
        if item_id is not None and int(item_id) == instrument_id:
            return {
                "bid": item.get("bid") or item.get("bidRate"),
                "ask": item.get("ask") or item.get("askRate"),
                "last": item.get("lastExecution") or item.get("last") or item.get("rate"),
            }
    if isinstance(payload, dict):
        return {
            "bid": payload.get("bid"),
            "ask": payload.get("ask"),
            "last": payload.get("lastExecution") or payload.get("last"),
        }
    return {}


def _normalize_candles(payload: Any) -> list[dict[str, Any]]:
    candles: list[Any] = []
    if isinstance(payload, dict):
        raw = payload.get("candles") or payload.get("data")
        if isinstance(raw, list):
            candles = raw
    elif isinstance(payload, list):
        candles = payload

    rows: list[dict[str, Any]] = []
    for candle in candles:
        if not isinstance(candle, dict):
            continue
        nested = candle.get("candles")
        if isinstance(nested, list) and nested:
            for inner in nested:
                if isinstance(inner, dict):
                    rows.append(_normalize_candle_row(inner))
            continue
        rows.append(_normalize_candle_row(candle))
    return rows


def _normalize_candle_row(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": candle.get("fromDate") or candle.get("timestamp") or candle.get("t"),
        "open": candle.get("open"),
        "high": candle.get("high"),
        "low": candle.get("low"),
        "close": candle.get("close"),
        "volume": candle.get("volume"),
    }
