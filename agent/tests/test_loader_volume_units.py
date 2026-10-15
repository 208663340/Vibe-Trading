"""Pin the volume-unit declarations of equity loaders (HKUDS/Vibe-Trading#1062).

Every A-share source declares board lots (1 lot = 100 shares) — baostock's
native single-share volume is normalized at its loader boundary; the
tencent/eastmoney loaders are market-dependent (A-share lots vs HK shares).
These pins lock the audit matrix from issue #1062 so a declaration cannot
drift silently — the cross-source 100x discrepancy that motivated the fix
was exactly such a silent drift.
"""

from __future__ import annotations

import pytest

from src.backtest.loaders.akshare_loader import DataLoader as AkshareLoader
from src.backtest.loaders.baostock_loader import DataLoader as BaostockLoader
from src.backtest.loaders.eastmoney_loader import DataLoader as EastmoneyLoader
from src.backtest.loaders.local_loader import DataLoader as LocalLoader
from src.backtest.loaders.mootdx_loader import DataLoader as MootdxLoader
from src.backtest.loaders.stooq_loader import DataLoader as StooqLoader
from src.backtest.loaders.tencent_loader import DataLoader as TencentLoader
from src.backtest.loaders.tushare import DataLoader as TushareLoader
from src.backtest.loaders.yahoo_loader import DataLoader as YahooLoader
from src.backtest.loaders.yfinance_loader import DataLoader as YfinanceLoader


@pytest.mark.parametrize(
    ("loader_cls", "market", "expected"),
    [
        (TencentLoader, "a_share", "lots"),
        (TencentLoader, "hk_equity", "shares"),
        (EastmoneyLoader, "a_share", "lots"),
        (EastmoneyLoader, "hk_equity", "shares"),
        (BaostockLoader, "a_share", "lots"),
        (MootdxLoader, "a_share", "lots"),
        (AkshareLoader, "a_share", "lots"),
        (TushareLoader, "a_share", "lots"),
        (YahooLoader, "us_equity", "shares"),
        (YahooLoader, "hk_equity", "shares"),
        (YfinanceLoader, "us_equity", "shares"),
        (YfinanceLoader, "hk_equity", "shares"),
        (StooqLoader, "us_equity", "shares"),
    ],
)
def test_loader_volume_unit_declaration(loader_cls, market, expected):
    assert getattr(loader_cls, "volume_units", {})[market] == expected


def test_undeclared_markets_surface_as_missing_not_wrong():
    """Markets without evidence stay undeclared (null), never guessed."""
    assert "crypto" not in YfinanceLoader.volume_units
    assert "us_equity" not in EastmoneyLoader.volume_units
    assert "hk_equity" not in TushareLoader.volume_units
    assert getattr(LocalLoader, "volume_units", {}) == {}
