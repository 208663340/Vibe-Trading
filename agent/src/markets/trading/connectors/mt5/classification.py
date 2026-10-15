"""Curated read/write classification for MT5 SDK operations.

Keys are the MetaTrader5 module functions the connector touches plus the
connector's own public order methods. Anything not listed and not a known
read resolves to WRITE (fail-closed) when the live gate consults this map.
"""

from __future__ import annotations

from src.markets.live.classification import ToolClass

#: MT5 operation read/write catalog. ``order_check`` is pinned WRITE
#: conservatively — it is only ever used inside the write path.
MT5_TOOL_CLASS: dict[str, ToolClass] = {
    # READ
    "account_info": ToolClass.READ,
    "positions_get": ToolClass.READ,
    "orders_get": ToolClass.READ,
    "history_deals_get": ToolClass.READ,
    "symbol_info": ToolClass.READ,
    "symbol_info_tick": ToolClass.READ,
    "symbols_get": ToolClass.READ,
    "copy_rates_from_pos": ToolClass.READ,
    "copy_rates_range": ToolClass.READ,
    # WRITE
    "order_send": ToolClass.WRITE,
    "order_check": ToolClass.WRITE,
    "place_order": ToolClass.WRITE,
    "cancel_order": ToolClass.WRITE,
    "close_position": ToolClass.WRITE,
    "modify_position": ToolClass.WRITE,
}
