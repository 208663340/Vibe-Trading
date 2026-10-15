"""Instrument search and symbol resolution for eToro.

eToro Public API conventions (see eToro builders docs):
- Exact ticker lookup: ``GET /market-data/search?internalSymbolFull=BTC``
- Fuzzy discovery: ``GET /market-data/search?search=<query>&limit=<n>``
- Asset-class browse: ``GET /market-data/instruments?instrumentTypeIds=<n>``
- Type catalog: ``GET /market-data/instrument-types``
- Search responses use ``items[]`` with ``instrumentId`` (lowercase ``d``).
- Metadata enrichment: ``GET /market-data/instruments?instrumentIds=...``
- Quotes (all profiles): ``GET /market-data/instruments/rates?instrumentIds=...``
"""

from __future__ import annotations

import re
from typing import Any

from src.markets.trading.connectors.etoro.client import (
    EtoroAPIError,
    EtoroConfig,
    MARKET_DATA_INSTRUMENTS_PATH,
    MARKET_DATA_INSTRUMENT_TYPES_PATH,
    MARKET_DATA_RATES_PATH,
    MARKET_DATA_SEARCH_PATH,
    make_client,
)

# Common aliases → eToro ``internalSymbolFull`` tickers.
_SYMBOL_ALIASES: dict[str, str] = {
    "BITCOIN": "BTC",
    "ETHEREUM": "ETH",
    "XRP": "XRP",
    "DOGECOIN": "DOGE",
    "SOLANA": "SOL",
    "LITECOIN": "LTC",
}

# Aggregate / sentinel ids returned by fuzzy search — never tradable.
_INVALID_INSTRUMENT_IDS = frozenset({-100000})

_TICKER_RE = re.compile(r"^[A-Za-z0-9./_-]{1,24}$")

# Cross-vocabulary aliases for labels that differ from API type names.
_SUPPLEMENTAL_TYPE_ALIASES: dict[str, int] = {
    "fx": 1,
    "equity": 5,
    "equities": 5,
}

_type_catalog_cache: tuple[dict[int, str], dict[str, int]] | None = None


def _base_payload(cfg: EtoroConfig) -> dict[str, Any]:
    return {
        "profile": cfg.profile,
        "environment": cfg.environment,
        "paper_guard": "path_separated_key_bound",
    }


def reset_instrument_type_cache() -> None:
    """Clear the cached instrument-type catalog (for unit tests)."""
    global _type_catalog_cache
    _type_catalog_cache = None


def get_instrument_types(config: EtoroConfig | None = None) -> dict[str, Any]:
    """Return the live ``instrumentTypeID`` catalog from market-data."""
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    id_to_label, _ = _load_instrument_type_catalog(cfg)
    rows = [
        {
            "instrument_type_id": type_id,
            "label": label,
            "name": label.replace("_", " "),
        }
        for type_id, label in sorted(id_to_label.items())
    ]
    return {"status": "ok", **_base_payload(cfg), "instrument_types": rows}


def _load_instrument_type_catalog(cfg: EtoroConfig) -> tuple[dict[int, str], dict[str, int]]:
    global _type_catalog_cache
    if _type_catalog_cache is not None:
        return _type_catalog_cache

    payload = make_client(cfg).request(
        "GET",
        MARKET_DATA_INSTRUMENT_TYPES_PATH,
        allow_retry=True,
    )
    rows = _extract_instrument_type_rows(payload)
    id_to_label: dict[int, str] = {}
    aliases: dict[str, int] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        type_id = _instrument_type_id(item)
        if type_id is None:
            continue
        description = str(item.get("instrumentTypeDescription") or item.get("description") or "").strip()
        label = _slugify_type_label(description) or str(type_id)
        id_to_label[type_id] = label
        for token in _alias_tokens_for_type(description, label):
            aliases.setdefault(token, type_id)

    for token, type_id in _SUPPLEMENTAL_TYPE_ALIASES.items():
        if type_id in id_to_label:
            aliases.setdefault(token, type_id)

    if not id_to_label:
        raise EtoroAPIError("instrument type catalog returned no rows")

    _type_catalog_cache = (id_to_label, aliases)
    return _type_catalog_cache


def _slugify_type_label(description: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(description or "").strip())
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


def _alias_tokens_for_type(description: str, slug: str) -> set[str]:
    tokens: set[str] = set()
    if slug:
        tokens.add(slug)
        tokens.add(slug.replace("_", ""))
        if slug.endswith("s") and len(slug) > 1:
            tokens.add(slug[:-1])
        else:
            tokens.add(f"{slug}s")
        if slug.endswith("y") and len(slug) > 1:
            tokens.add(f"{slug[:-1]}ies")
    clean = str(description or "").strip()
    if clean:
        tokens.add(clean.lower())
        tokens.add(clean.lower().replace(" ", "_"))
        for word in re.findall(r"[A-Za-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", clean)):
            if len(word) > 1:
                tokens.add(word.lower())
    return {token for token in tokens if token}


def _extract_instrument_type_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("instrumentTypes", "items", "data", "types"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def search_instruments(
    query: str,
    config: EtoroConfig | None = None,
    *,
    limit: int = 10,
    mode: str = "auto",
    instrument_type_id: int | None = None,
    include_rates: bool = False,
) -> dict[str, Any]:
    """Search eToro instruments by ticker, free-text query, or asset class.

    Args:
        query: Symbol (``BTC``, ``AAPL``), discovery text, or asset-class label
            (``crypto``, ``stocks``, ``forex``).
        config: Optional connector config.
        limit: Max results (capped at 50).
        mode: ``auto`` (ticker → exact lookup then fuzzy), ``symbol`` (exact only),
            ``discover`` (fuzzy ``search`` param only), or ``type`` (browse by
            ``instrument_type_id`` / asset-class alias).
        instrument_type_id: Optional eToro ``instrumentTypeID`` filter (e.g. ``10``
            for crypto). When set, uses ``GET /market-data/instruments`` with
            ``instrumentTypeIds``.
        include_rates: When browsing by type, attach bid/ask/last from the flat
            ``/market-data/instruments/rates`` endpoint (works on all profiles).
    """
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    token = str(query or "").strip()
    if not token:
        raise EtoroAPIError("search query is required")

    clean_limit = max(1, min(int(limit), 50))
    clean_mode = str(mode or "auto").strip().lower()
    if clean_mode not in ("auto", "symbol", "discover", "type"):
        raise EtoroAPIError("mode must be 'auto', 'symbol', 'discover', or 'type'")

    if clean_mode == "type" or instrument_type_id is not None:
        resolved_type_id = instrument_type_id
        if resolved_type_id is None:
            resolved_type_id = _instrument_type_id_from_query(token, cfg)
        if resolved_type_id is None:
            raise EtoroAPIError(
                "instrument_type_id is required for type browse "
                "(e.g. 10 for crypto) or use an asset-class query like 'crypto'"
            )
        return list_instruments_by_type(
            resolved_type_id,
            cfg,
            limit=clean_limit,
            include_rates=include_rates,
        )

    rows: list[dict[str, Any]] = []
    lookup_mode = clean_mode
    if clean_mode == "auto":
        lookup_mode = "symbol" if _looks_like_ticker(token) else "discover"

    if lookup_mode == "symbol":
        rows = _search_by_symbol(cfg, token, limit=clean_limit)
        if not rows and clean_mode == "auto":
            type_id = _instrument_type_id_from_query(token, cfg)
            if type_id is not None:
                return list_instruments_by_type(
                    type_id,
                    cfg,
                    limit=clean_limit,
                    include_rates=include_rates,
                )
            rows = _search_by_text(cfg, token, limit=clean_limit)
    else:
        rows = _search_by_text(cfg, token, limit=clean_limit)

    normalized = [_normalize_instrument_row(item) for item in rows if isinstance(item, dict)]
    normalized = [row for row in normalized if row.get("instrument_id") is not None]
    canonical = _canonical_ticker(token)
    normalized.sort(
        key=lambda row: -_match_priority(
            row.get("raw") or {},
            canonical,
            canonical,
            token,
        ),
    )
    return {
        "status": "ok",
        **_base_payload(cfg),
        "query": token,
        "mode": lookup_mode,
        "instruments": normalized[:clean_limit],
    }


def list_instruments_by_type(
    instrument_type_id: int,
    config: EtoroConfig | None = None,
    *,
    limit: int = 10,
    include_rates: bool = False,
) -> dict[str, Any]:
    """List tradable instruments for an eToro ``instrumentTypeID`` (e.g. ``10`` = crypto)."""
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    type_id = int(instrument_type_id)
    id_to_label, _ = _load_instrument_type_catalog(cfg)
    if type_id not in id_to_label:
        raise EtoroAPIError(f"unsupported instrument_type_id {type_id}")

    clean_limit = max(1, min(int(limit), 50))
    payload = make_client(cfg).request(
        "GET",
        MARKET_DATA_INSTRUMENTS_PATH,
        params={"instrumentTypeIds": type_id},
        allow_retry=True,
    )
    items = _extract_metadata_items(payload)
    filtered = [
        item
        for item in items
        if isinstance(item, dict) and _instrument_type_id(item) == type_id
    ]
    normalized = [_normalize_metadata_row(item) for item in filtered]
    normalized = [row for row in normalized if row.get("instrument_id") is not None]
    instruments = normalized[:clean_limit]
    if include_rates:
        _attach_rates(cfg, instruments)

    return {
        "status": "ok",
        **_base_payload(cfg),
        "instrument_type_id": type_id,
        "instrument_type": id_to_label[type_id],
        "mode": "type",
        "instruments": instruments,
    }


def resolve_instrument_id(symbol: str, cfg: EtoroConfig) -> int:
    """Resolve a user symbol to an eToro ``instrumentId`` for trading APIs."""
    token = str(symbol or "").strip()
    if not token:
        raise EtoroAPIError("symbol is required")
    if token.isdigit():
        instrument_id = int(token)
        if instrument_id in _INVALID_INSTRUMENT_IDS or instrument_id < 0:
            raise EtoroAPIError(f"invalid instrument id {instrument_id}")
        return instrument_id

    canonical = _canonical_ticker(token)
    candidates: list[tuple[int, int]] = []  # (priority, instrument_id)

    for lookup in _lookup_variants(token, canonical):
        for item in _search_by_symbol(cfg, lookup, limit=25):
            instrument_id = _instrument_id(item)
            if instrument_id is None:
                continue
            priority = _match_priority(item, lookup, canonical, token)
            if priority > 0:
                candidates.append((priority, instrument_id))

    if not candidates:
        for item in _search_by_text(cfg, canonical, limit=25):
            instrument_id = _instrument_id(item)
            if instrument_id is None:
                continue
            priority = _match_priority(item, canonical, canonical, token)
            if priority > 0:
                candidates.append((priority, instrument_id))

    if not candidates:
        raise EtoroAPIError(f"instrument not found for symbol {token!r}")

    candidates.sort(key=lambda pair: (-pair[0], pair[1]))
    return candidates[0][1]


def get_instrument_metadata(
    instrument_ids: list[int] | tuple[int, ...],
    config: EtoroConfig | None = None,
) -> dict[str, Any]:
    """Fetch display metadata for one or more instrument ids (batch ≤ 50)."""
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    ids = [int(i) for i in instrument_ids if int(i) not in _INVALID_INSTRUMENT_IDS and int(i) > 0]
    if not ids:
        raise EtoroAPIError("at least one valid instrument_id is required")
    if len(ids) > 50:
        raise EtoroAPIError("instrumentIds batch limit is 50")

    payload = make_client(cfg).request(
        "GET",
        MARKET_DATA_INSTRUMENTS_PATH,
        params={"instrumentIds": ",".join(str(i) for i in ids)},
        allow_retry=True,
    )
    items = _extract_metadata_items(payload)
    instruments = [_normalize_metadata_row(item) for item in items if isinstance(item, dict)]
    return {"status": "ok", **_base_payload(cfg), "instruments": instruments}


def _canonical_ticker(token: str) -> str:
    upper = token.strip().upper()
    return _SYMBOL_ALIASES.get(upper, upper)


def _looks_like_ticker(token: str) -> bool:
    return bool(_TICKER_RE.fullmatch(token.strip()))


def _lookup_variants(token: str, canonical: str) -> list[str]:
    upper = token.strip().upper()
    if upper != canonical:
        return [canonical]
    variants = [canonical]
    raw = token.strip()
    if raw and raw.upper() != canonical:
        variants.append(raw.upper())
    return variants


def _search_by_symbol(cfg: EtoroConfig, symbol: str, *, limit: int) -> list[dict[str, Any]]:
    payload = make_client(cfg).request(
        "GET",
        MARKET_DATA_SEARCH_PATH,
        params={"internalSymbolFull": symbol},
        allow_retry=True,
    )
    items = _extract_items(payload)
    return [item for item in items if isinstance(item, dict)]


def _search_by_text(cfg: EtoroConfig, query: str, *, limit: int) -> list[dict[str, Any]]:
    payload = make_client(cfg).request(
        "GET",
        MARKET_DATA_SEARCH_PATH,
        params={"search": query, "limit": limit},
        allow_retry=True,
    )
    items = _extract_items(payload)
    return [item for item in items if isinstance(item, dict)]


def _instrument_type_id(item: dict[str, Any]) -> int | None:
    for key in ("instrumentTypeID", "instrumentTypeId", "instrument_type_id"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _instrument_type_id_from_query(query: str, cfg: EtoroConfig) -> int | None:
    token = str(query or "").strip().lower().replace("-", "_")
    if not token:
        return None
    supplemental = _SUPPLEMENTAL_TYPE_ALIASES.get(token)
    if supplemental is not None:
        return supplemental
    id_to_label, aliases = _load_instrument_type_catalog(cfg)
    if token.isdigit():
        type_id = int(token)
        return type_id if type_id in id_to_label else None
    return aliases.get(token)


def _attach_rates(cfg: EtoroConfig, instruments: list[dict[str, Any]]) -> None:
    ids: list[int] = []
    for row in instruments:
        instrument_id = row.get("instrument_id")
        if instrument_id is None:
            continue
        try:
            ids.append(int(instrument_id))
        except (TypeError, ValueError):
            continue
    if not ids:
        return
    payload = make_client(cfg).request(
        "GET",
        MARKET_DATA_RATES_PATH,
        params={"instrumentIds": ",".join(str(i) for i in ids)},
        allow_retry=True,
    )
    rates = _rates_by_instrument_id(payload)
    for row in instruments:
        instrument_id = row.get("instrument_id")
        if instrument_id is None:
            continue
        try:
            quote = rates.get(int(instrument_id))
        except (TypeError, ValueError):
            quote = None
        if quote:
            row["quote"] = quote


def _rates_by_instrument_id(payload: Any) -> dict[int, dict[str, Any]]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("rates", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                items = value
                break
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        instrument_id = item.get("instrumentID") or item.get("instrumentId")
        if instrument_id is None:
            continue
        try:
            key = int(instrument_id)
        except (TypeError, ValueError):
            continue
        result[key] = {
            "bid": item.get("bid") or item.get("bidRate"),
            "ask": item.get("ask") or item.get("askRate"),
            "last": item.get("lastExecution") or item.get("last") or item.get("rate"),
        }
    return result


def _instrument_id(item: dict[str, Any]) -> int | None:
    for key in ("instrumentId", "instrumentID", "internalInstrumentId"):
        value = item.get(key)
        if value is None:
            continue
        try:
            instrument_id = int(value)
        except (TypeError, ValueError):
            continue
        if instrument_id in _INVALID_INSTRUMENT_IDS or instrument_id < 0:
            return None
        return instrument_id
    return None


def _symbol_full(item: dict[str, Any]) -> str:
    return str(
        item.get("internalSymbolFull")
        or item.get("symbolFull")
        or item.get("symbol")
        or ""
    ).strip()


def _display_name(item: dict[str, Any]) -> str:
    return str(
        item.get("internalInstrumentDisplayName")
        or item.get("instrumentDisplayName")
        or item.get("displayName")
        or ""
    ).strip()


def _match_priority(item: dict[str, Any], lookup: str, canonical: str, original: str) -> int:
    sym = _symbol_full(item).upper()
    display = _display_name(item).upper()
    lookup_u = lookup.upper()
    canonical_u = canonical.upper()
    original_u = original.upper()

    if sym == lookup_u or sym == canonical_u:
        return 100
    if sym == original_u:
        return 95
    if display == original_u or display == canonical_u:
        return 90
    if canonical_u in display and sym.startswith(canonical_u):
        return 50
    if lookup_u in sym:
        return 30
    return 0


def _normalize_instrument_row(item: dict[str, Any]) -> dict[str, Any]:
    instrument_id = _instrument_id(item)
    symbol = _symbol_full(item)
    display = _display_name(item)
    return {
        "instrument_id": instrument_id,
        "symbol": symbol or display,
        "display_name": display,
        "internal_symbol_full": item.get("internalSymbolFull") or item.get("symbolFull"),
        "is_buy_enabled": item.get("isBuyEnabled"),
        "is_tradable": item.get("isCurrentlyTradable"),
        "raw": item,
    }


def _normalize_metadata_row(item: dict[str, Any]) -> dict[str, Any]:
    instrument_id = _instrument_id(item)
    return {
        "instrument_id": instrument_id,
        "symbol": _symbol_full(item) or item.get("symbolFull"),
        "display_name": _display_name(item) or item.get("instrumentDisplayName"),
        "instrument_type_id": _instrument_type_id(item),
        "exchange_id": item.get("exchangeID") or item.get("exchangeId"),
        "raw": item,
    }


def _extract_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "data", "instruments", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_metadata_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("instrumentDisplayDatas", "items", "data", "instruments"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []
