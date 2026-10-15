"""Trading connector profiles and operations."""

from src.markets.trading.profiles import (
    list_profiles,
    load_selected_profile_id,
    profile_by_id,
    save_selected_profile_id,
)
from src.markets.trading.service import (
    check_connection,
    get_account,
    get_history,
    get_open_orders,
    get_positions,
    get_quote,
)
from src.markets.trading.types import TradingProfile

__all__ = [
    "TradingProfile",
    "check_connection",
    "get_account",
    "get_history",
    "get_open_orders",
    "get_positions",
    "get_quote",
    "list_profiles",
    "load_selected_profile_id",
    "profile_by_id",
    "save_selected_profile_id",
]
