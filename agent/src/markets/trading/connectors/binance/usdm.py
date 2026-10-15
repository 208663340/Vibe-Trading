"""Strict shaping for read-only Binance USD-M account observations.

The caller owns the authenticated ccxt client and host guard.  This module only
invokes the two allowlisted account reads and joins their responses; it cannot
create a connection or submit an order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
import hashlib
import json
import math
from typing import Any
from urllib.parse import urlparse


SCHEMA_VERSION = "binance-usdm-account-observation-v1"
FIDELITY_FLAGS = ["client_observation_time", "sequential_signed_reads"]
DEFAULT_OBSERVATION_ABSOLUTE_TOLERANCE = 1e-4
OBSERVATION_RELATIVE_TOLERANCE = 1e-8
CloseEnough = Callable[[float, float], bool]


class UsdMObservationError(ValueError):
    """Raised when Binance reports an unsupported or incoherent account state."""


def assert_exchange_endpoints(exchange: Any) -> None:
    """Require exact HTTPS base URLs for the two signed USD-M reads."""
    urls = getattr(exchange, "urls", None)
    api_urls = urls.get("api") if isinstance(urls, Mapping) else None
    expected_paths = {
        "fapiPrivateV2": "/fapi/v2",
        "fapiPrivateV3": "/fapi/v3",
    }
    for endpoint, expected_path in expected_paths.items():
        url = str(api_urls.get(endpoint, "")) if isinstance(api_urls, Mapping) else ""
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "fapi.binance.com"
            or parsed.port is not None
            or parsed.path.rstrip("/") != expected_path
            or parsed.query
            or parsed.fragment
        ):
            raise UsdMObservationError(
                f"Binance USD-M endpoint '{endpoint}' resolved to unapproved host or path '{url}'."
            )


def read_account_observation(
    exchange: Any,
    *,
    source_profile: str,
    host: str,
    now: Callable[[], datetime],
    absolute_tolerance: float = DEFAULT_OBSERVATION_ABSOLUTE_TOLERANCE,
) -> dict[str, Any]:
    """Read and normalize one fail-closed, single-asset USD-M observation."""
    if not math.isfinite(absolute_tolerance) or absolute_tolerance < 0:
        raise UsdMObservationError("observation absolute tolerance must be non-negative and finite")

    def close_enough(left: float, right: float) -> bool:
        return math.isclose(
            left,
            right,
            rel_tol=OBSERVATION_RELATIVE_TOLERANCE,
            abs_tol=absolute_tolerance,
        )

    started_at = now()
    account = exchange.fapiprivatev2_get_account()
    position_risk = exchange.fapiprivatev3_get_positionrisk()
    observed_at = now()
    if not isinstance(account, Mapping) or not isinstance(position_risk, list):
        raise UsdMObservationError("Binance USD-M returned an invalid account payload")

    account_values = _account_values(account, close_enough)
    positions = _join_positions(account, position_risk, close_enough)
    _require_coherent_totals(account_values, positions, close_enough)
    configuration_hash = _configuration_hash(source_profile, host, absolute_tolerance)
    return {
        "status": "ok",
        "profile": "live-readonly",
        "source_profile": source_profile,
        "market_type": "usdm",
        "source": "binance-usdm",
        "host": host,
        "paper_guard": "host_separated",
        "schema_version": SCHEMA_VERSION,
        "configuration_hash": configuration_hash,
        "observation_started_at": started_at.isoformat(),
        "observed_at": observed_at.isoformat(),
        "observation_span_seconds": (observed_at - started_at).total_seconds(),
        "account": account_values,
        "positions": positions,
        "fidelity_flags": list(FIDELITY_FLAGS),
    }


def _account_values(account: Mapping[str, Any], close_enough: CloseEnough) -> dict[str, float]:
    if account.get("multiAssetsMargin") is not False:
        raise UsdMObservationError("Binance USD-M Shadow Account does not support multi-asset margin")
    usdt_asset = _require_usdt_assets(account)
    open_order_margin = _number(
        account.get("totalOpenOrderInitialMargin"),
        "totalOpenOrderInitialMargin",
        non_negative=True,
    )
    if open_order_margin != 0:
        raise UsdMObservationError("Binance USD-M Shadow Account requires zero open-order margin")
    values = {
        "wallet_balance": _number(account.get("totalWalletBalance"), "totalWalletBalance"),
        "margin_balance": _number(account.get("totalMarginBalance"), "totalMarginBalance"),
        "available_balance": _number(account.get("availableBalance"), "availableBalance"),
        "total_unrealized_pnl": _number(account.get("totalUnrealizedProfit"), "totalUnrealizedProfit"),
        "total_initial_margin": _number(
            account.get("totalPositionInitialMargin"),
            "totalPositionInitialMargin",
            non_negative=True,
        ),
        "total_maintenance_margin": _number(account.get("totalMaintMargin"), "totalMaintMargin", non_negative=True),
        "open_order_initial_margin": open_order_margin,
    }
    asset_fields = {
        "wallet_balance": "walletBalance",
        "margin_balance": "marginBalance",
        "available_balance": "availableBalance",
        "total_unrealized_pnl": "unrealizedProfit",
        "total_initial_margin": "positionInitialMargin",
        "total_maintenance_margin": "maintMargin",
        "open_order_initial_margin": "openOrderInitialMargin",
    }
    if any(
        not close_enough(values[name], _number(usdt_asset.get(field), field)) for name, field in asset_fields.items()
    ):
        raise UsdMObservationError("Binance USD-M account totals and USDT asset totals are incoherent")
    return values


def _join_positions(
    account: Mapping[str, Any],
    position_risk: list[Any],
    close_enough: CloseEnough,
) -> list[dict[str, Any]]:
    account_rows = _mapping_rows(account.get("positions"), "account positions")
    risk_rows = _mapping_rows(position_risk, "position risk")
    _require_one_way(account_rows)
    _require_one_way(risk_rows)

    account_active = _active_by_symbol(account_rows, "account")
    risk_active = _active_by_symbol(risk_rows, "position risk")
    if set(account_active) != set(risk_active):
        raise UsdMObservationError("Binance USD-M position reads are incoherent")

    result: list[dict[str, Any]] = []
    for raw_symbol in sorted(account_active):
        account_row = account_active[raw_symbol]
        risk_row = risk_active[raw_symbol]
        quantity = _number(account_row.get("positionAmt"), "positionAmt")
        risk_quantity = _number(risk_row.get("positionAmt"), "positionAmt")
        entry_price = _number(account_row.get("entryPrice"), "entryPrice", positive=True)
        risk_entry = _number(risk_row.get("entryPrice"), "entryPrice", positive=True)
        if not math.isclose(quantity, risk_quantity, rel_tol=0, abs_tol=1e-12) or not math.isclose(
            entry_price, risk_entry, rel_tol=0, abs_tol=1e-12
        ):
            raise UsdMObservationError("Binance USD-M position reads are incoherent")
        open_order_margins = (
            _number(
                account_row.get("openOrderInitialMargin"),
                "openOrderInitialMargin",
                non_negative=True,
            ),
            _number(
                risk_row.get("openOrderInitialMargin"),
                "openOrderInitialMargin",
                non_negative=True,
            ),
        )
        if any(open_order_margins):
            raise UsdMObservationError("Binance USD-M Shadow Account requires zero open-order margin")
        for account_field, risk_field in (
            ("positionInitialMargin", "positionInitialMargin"),
            ("maintMargin", "maintMargin"),
            ("unrealizedProfit", "unRealizedProfit"),
        ):
            if not close_enough(
                _number(account_row.get(account_field), account_field),
                _number(risk_row.get(risk_field), risk_field),
            ):
                raise UsdMObservationError("Binance USD-M position reads are incoherent")
        if str(risk_row.get("marginAsset") or "").upper() != "USDT":
            raise UsdMObservationError("Binance USD-M Shadow Account supports USDT collateral only")
        isolated = account_row.get("isolated")
        if not isinstance(isolated, bool):
            raise UsdMObservationError("Binance USD-M margin mode is missing")
        isolated_margin = _number(
            risk_row.get("isolatedMargin"),
            "isolatedMargin",
            non_negative=True,
        )
        if isolated and isolated_margin == 0:
            raise UsdMObservationError("Binance USD-M isolated position requires positive isolated margin")
        if not isolated and isolated_margin != 0:
            raise UsdMObservationError("Binance USD-M cross position must report zero isolated margin")
        result.append(
            {
                "symbol": _canonical_symbol(raw_symbol),
                "quantity": quantity,
                "entry_price": entry_price,
                "leverage": _number(account_row.get("leverage"), "leverage", positive=True),
                "margin_mode": "isolated" if isolated else "cross",
                "isolated_margin": isolated_margin if isolated else None,
                "unrealized_pnl": _number(risk_row.get("unRealizedProfit"), "unRealizedProfit"),
                "initial_margin": _number(
                    risk_row.get("positionInitialMargin"),
                    "positionInitialMargin",
                    non_negative=True,
                ),
                "maintenance_margin": _number(risk_row.get("maintMargin"), "maintMargin", non_negative=True),
                "update_time": _integer(risk_row.get("updateTime"), "updateTime"),
            }
        )
    return result


def _require_usdt_assets(account: Mapping[str, Any]) -> Mapping[str, Any]:
    assets = _mapping_rows(account.get("assets"), "assets")
    usdt_asset: Mapping[str, Any] | None = None
    balance_fields = (
        "walletBalance",
        "marginBalance",
        "availableBalance",
        "initialMargin",
        "positionInitialMargin",
        "openOrderInitialMargin",
        "maintMargin",
        "unrealizedProfit",
    )
    for asset in assets:
        symbol = str(asset.get("asset") or "").upper()
        balances = tuple(_number(asset.get(field), field) for field in balance_fields)
        if symbol == "USDT":
            if usdt_asset is not None:
                raise UsdMObservationError("Binance USD-M assets must include exactly one USDT row")
            usdt_asset = asset
        elif any(value != 0 for value in balances):
            raise UsdMObservationError("Binance USD-M Shadow Account supports the USDT asset only")
    if usdt_asset is None:
        raise UsdMObservationError("Binance USD-M assets must include USDT")
    return usdt_asset


def _require_coherent_totals(
    account: Mapping[str, float],
    positions: list[dict[str, Any]],
    close_enough: CloseEnough,
) -> None:
    comparisons = (
        (
            account["total_unrealized_pnl"],
            sum(row["unrealized_pnl"] for row in positions),
        ),
        (
            account["total_initial_margin"],
            sum(row["initial_margin"] for row in positions),
        ),
        (
            account["total_maintenance_margin"],
            sum(row["maintenance_margin"] for row in positions),
        ),
        (
            account["margin_balance"],
            account["wallet_balance"] + account["total_unrealized_pnl"],
        ),
    )
    if any(not close_enough(reported, derived) for reported, derived in comparisons):
        raise UsdMObservationError("Binance USD-M account totals are incoherent")


def _mapping_rows(value: Any, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise UsdMObservationError(f"Binance USD-M {name} payload is invalid")
    return value


def _require_one_way(rows: list[Mapping[str, Any]]) -> None:
    if any(str(row.get("positionSide") or "").upper() != "BOTH" for row in rows):
        raise UsdMObservationError("Binance USD-M Shadow Account requires one-way position mode")


def _active_by_symbol(rows: list[Mapping[str, Any]], source: str) -> dict[str, Mapping[str, Any]]:
    active: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        quantity = _number(row.get("positionAmt"), "positionAmt")
        if quantity == 0:
            continue
        symbol = str(row.get("symbol") or "").upper()
        _canonical_symbol(symbol)
        if symbol in active:
            raise UsdMObservationError(f"duplicate {source} position symbol")
        active[symbol] = row
    return active


def _canonical_symbol(symbol: str) -> str:
    if not symbol.endswith("USDT") or len(symbol) <= 4 or not symbol.isalnum():
        raise UsdMObservationError("Binance USD-M supports canonical */USDT symbols only")
    return f"{symbol[:-4]}-USDT-PERP"


def _number(
    value: Any,
    name: str,
    *,
    non_negative: bool = False,
    positive: bool = False,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise UsdMObservationError(f"Binance USD-M field {name} must be numeric") from exc
    if not math.isfinite(number):
        raise UsdMObservationError(f"Binance USD-M field {name} must be finite")
    if positive and number <= 0:
        raise UsdMObservationError(f"Binance USD-M field {name} must be positive")
    if non_negative and number < 0:
        raise UsdMObservationError(f"Binance USD-M field {name} must be non-negative")
    return number


def _integer(value: Any, name: str) -> int:
    number = _number(value, name, non_negative=True)
    if not number.is_integer():
        raise UsdMObservationError(f"Binance USD-M field {name} must be an integer")
    return int(number)


def _configuration_hash(source_profile: str, host: str, absolute_tolerance: float) -> str:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source_profile": source_profile,
        "market_type": "usdm",
        "host": host,
        "account_endpoint": "/fapi/v2/account",
        "position_endpoint": "/fapi/v3/positionRisk",
        "observation_absolute_tolerance": absolute_tolerance,
        "observation_relative_tolerance": OBSERVATION_RELATIVE_TOLERANCE,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
