"""Curated read/write classification for eToro Public API operations.

Unlisted tool names are classified as UNKNOWN and fail closed as writes by the
live registry (see ``src.markets.live.registry``).
"""

from __future__ import annotations

from src.markets.live.classification import ToolClass

ETORO_TOOL_CLASS: dict[str, ToolClass] = {
    # READ
    "check_status": ToolClass.READ,
    "get_user_profile": ToolClass.READ,
    "get_account_snapshot": ToolClass.READ,
    "get_positions": ToolClass.READ,
    "get_open_orders": ToolClass.READ,
    "get_quote": ToolClass.READ,
    "get_historical_bars": ToolClass.READ,
    "search_instruments": ToolClass.READ,
    "list_instruments_by_type": ToolClass.READ,
    "get_instrument_types": ToolClass.READ,
    "get_instrument_metadata": ToolClass.READ,
    "copy_poll": ToolClass.READ,
    "copy_precheck": ToolClass.READ,
    # WRITE — trading
    "place_order": ToolClass.WRITE,
    "cancel_order": ToolClass.WRITE,
    "close_position": ToolClass.WRITE,
    "cancel_close_order": ToolClass.WRITE,
    "edit_position_stops": ToolClass.WRITE,
    # WRITE — copy
    "copy_start_or_adjust": ToolClass.WRITE,
    "copy_close": ToolClass.WRITE,
}
