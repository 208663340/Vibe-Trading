"""eToro trading write operations (open, close, cancel, edit)."""

from __future__ import annotations

import math
import uuid
from typing import Any

from src.markets.trading.connectors.etoro.client import (
    EtoroAPIError,
    EtoroConfig,
    execution_root,
    execution_v1_root,
    make_client,
    positions_root,
)
from src.markets.trading.connectors.etoro.instruments import resolve_instrument_id, _looks_like_ticker


def _base(cfg: EtoroConfig) -> dict[str, Any]:
    return {
        "profile": cfg.profile,
        "environment": cfg.environment,
        "paper_guard": "path_separated_key_bound",
    }


def _order_error(cfg: EtoroConfig, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": message, **_base(cfg), **extra}


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
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    clean_side = str(side or "").strip().lower()
    if clean_side not in ("buy", "sell"):
        return _order_error(cfg, "side must be 'buy' or 'sell'", symbol=symbol)

    has_qty = quantity is not None
    has_notional = notional is not None
    if has_qty == has_notional:
        return _order_error(cfg, "exactly one of quantity or notional is required", symbol=symbol, side=clean_side)

    clean_type = str(order_type or "market").strip().lower()
    if clean_type not in ("market", "limit"):
        return _order_error(cfg, "order_type must be 'market' or 'limit'", symbol=symbol, side=clean_side)
    if clean_type == "limit" and limit_price is None:
        # eToro limit orders are MIT orders keyed on TriggerRate; without one
        # the order rests untriggered while looking accepted.
        return _order_error(cfg, "limit orders require limit_price", symbol=symbol, side=clean_side)

    try:
        instrument_ref = _order_instrument_ref(symbol, cfg)
    except EtoroAPIError as exc:
        return _order_error(cfg, str(exc), symbol=symbol, side=clean_side)

    body: dict[str, Any] = {
        "action": "open",
        "transaction": clean_side,
        **instrument_ref,
        "orderType": "mkt" if clean_type == "market" else "mit",
        "orderCurrency": "usd",
        "leverage": 1,
        "stopLossType": "fixed",
    }
    if has_notional:
        body["amount"] = float(notional)
    else:
        body["units"] = float(quantity)
    if clean_type == "limit" and limit_price is not None:
        body["TriggerRate"] = float(limit_price)

    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{execution_root(cfg)}/orders"
    try:
        payload = make_client(cfg).request("POST", path, json_body=body, request_id=req_id)
    except EtoroAPIError as exc:
        return _order_error(cfg, str(exc), symbol=symbol, side=clean_side, request_id=req_id)

    order_id = _extract_order_id(payload)
    instrument_id = instrument_ref.get("instrumentId")
    return {
        "status": "ok",
        **_base(cfg),
        "symbol": symbol,
        "instrument_id": instrument_id,
        "side": clean_side,
        "order_type": clean_type,
        "time_in_force": time_in_force,
        "order_id": order_id,
        "request_id": req_id,
        "raw": payload,
    }


def cancel_order(
    config: EtoroConfig | None = None,
    order_id: str = "",
    *,
    symbol: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    clean_id = str(order_id or "").strip()
    if not clean_id:
        return _order_error(cfg, "order_id is required", symbol=symbol)

    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{execution_root(cfg)}/orders/{clean_id}"
    try:
        payload = make_client(cfg).request("DELETE", path, request_id=req_id)
    except EtoroAPIError as exc:
        return _order_error(cfg, str(exc), order_id=clean_id, symbol=symbol, request_id=req_id)

    return {
        "status": "ok",
        **_base(cfg),
        "order_id": clean_id,
        "symbol": symbol,
        "request_id": req_id,
        "raw": payload,
    }


def close_position(
    config: EtoroConfig | None = None,
    *,
    position_id: int | str,
    instrument_id: int | None = None,
    units_to_close: float | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    pos_id = str(position_id).strip()
    if not pos_id:
        return _order_error(cfg, "position_id is required")

    if instrument_id is None:
        return _order_error(
            cfg,
            "instrument_id is required by the eToro close-position contract",
            position_id=pos_id,
        )
    try:
        clean_instrument_id = int(instrument_id)
    except (TypeError, ValueError, OverflowError):
        return _order_error(cfg, "instrument_id must be a positive integer", position_id=pos_id)
    if clean_instrument_id <= 0:
        return _order_error(cfg, "instrument_id must be a positive integer", position_id=pos_id)

    body: dict[str, Any] = {"InstrumentId": clean_instrument_id}
    if units_to_close is not None:
        try:
            clean_units = float(units_to_close)
        except (TypeError, ValueError, OverflowError):
            return _order_error(cfg, "units_to_close must be a finite positive number", position_id=pos_id)
        if not math.isfinite(clean_units) or clean_units <= 0:
            return _order_error(cfg, "units_to_close must be a finite positive number", position_id=pos_id)
        body["UnitsToDeduct"] = clean_units

    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{execution_v1_root(cfg)}/market-close-orders/positions/{pos_id}"
    try:
        payload = make_client(cfg).request("POST", path, json_body=body, request_id=req_id)
    except EtoroAPIError as exc:
        return _order_error(cfg, str(exc), position_id=pos_id, request_id=req_id)

    return {
        "status": "ok",
        **_base(cfg),
        "position_id": pos_id,
        "units_to_close": units_to_close,
        "request_id": req_id,
        "order_id": _extract_order_id(payload),
        "raw": payload,
    }


def cancel_close_order(
    config: EtoroConfig | None = None,
    order_id: str = "",
    *,
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    clean_id = str(order_id or "").strip()
    if not clean_id:
        return _order_error(cfg, "order_id is required")

    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{execution_v1_root(cfg)}/market-close-orders/{clean_id}"
    try:
        payload = make_client(cfg).request("DELETE", path, request_id=req_id)
    except EtoroAPIError as exc:
        return _order_error(cfg, str(exc), order_id=clean_id, request_id=req_id)

    return {"status": "ok", **_base(cfg), "order_id": clean_id, "request_id": req_id, "raw": payload}


def edit_position_stops(
    config: EtoroConfig | None = None,
    *,
    position_id: int | str,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    trailing_stop_loss: bool | None = None,
    clear_stop_loss: bool = False,
    clear_take_profit: bool = False,
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    pos_id = str(position_id).strip()
    if not pos_id:
        return _order_error(cfg, "position_id is required")
    if (
        stop_loss is None
        and take_profit is None
        and trailing_stop_loss is None
        and not clear_stop_loss
        and not clear_take_profit
    ):
        return _order_error(
            cfg,
            "at least one stop-loss/take-profit update is required",
        )
    if stop_loss is not None and clear_stop_loss:
        return _order_error(cfg, "stop_loss and clear_stop_loss are mutually exclusive")
    if take_profit is not None and clear_take_profit:
        return _order_error(cfg, "take_profit and clear_take_profit are mutually exclusive")

    body: dict[str, Any] = {}
    if stop_loss is not None:
        try:
            value = float(stop_loss)
        except (TypeError, ValueError, OverflowError):
            return _order_error(cfg, "stop_loss must be a finite positive rate")
        if not math.isfinite(value) or value <= 0:
            return _order_error(cfg, "stop_loss must be a finite positive rate")
        body["stopLossRate"] = value
    if take_profit is not None:
        try:
            value = float(take_profit)
        except (TypeError, ValueError, OverflowError):
            return _order_error(cfg, "take_profit must be a finite positive rate")
        if not math.isfinite(value) or value <= 0:
            return _order_error(cfg, "take_profit must be a finite positive rate")
        body["takeProfitRate"] = value
    if trailing_stop_loss is not None:
        body["stopLossType"] = "trailing" if trailing_stop_loss else "fixed"
    if clear_stop_loss:
        body["clearStopLoss"] = True
    if clear_take_profit:
        body["clearTakeProfit"] = True

    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{positions_root(cfg)}/positions/{pos_id}"
    try:
        payload = make_client(cfg).request("PATCH", path, json_body=body, request_id=req_id)
    except EtoroAPIError as exc:
        return _order_error(cfg, str(exc), position_id=pos_id, request_id=req_id)

    return {"status": "ok", **_base(cfg), "position_id": pos_id, "request_id": req_id, "raw": payload}


def _order_instrument_ref(symbol: str, cfg: EtoroConfig) -> dict[str, Any]:
    """Build exactly one of ``symbol`` or ``instrumentId`` for open-order bodies."""
    token = str(symbol or "").strip()
    if not token:
        raise EtoroAPIError("symbol is required")
    if _looks_like_ticker(token):
        return {"symbol": token}
    instrument_id = resolve_instrument_id(token, cfg)
    return {"instrumentId": instrument_id}


def _extract_order_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("orderID", "orderId", "order_id", "id"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("orderID", "orderId", "order_id", "id"):
            value = data.get(key)
            if value is not None:
                return str(value)
    order_for_close = payload.get("orderForClose")
    if isinstance(order_for_close, dict):
        value = order_for_close.get("orderID") or order_for_close.get("orderId")
        if value is not None:
            return str(value)
    return None
