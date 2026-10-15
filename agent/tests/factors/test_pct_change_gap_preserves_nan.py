"""Regression: a factor's internal ``returns`` feature must propagate NaN
across a genuine missing-close gap, not fabricate a value.

close.pct_change() with no fill_method argument defaults (pandas 2.x,
deprecated) to fill_method='pad', which forward-fills the missing close
before differencing — turning a real data gap (a delisting, a market
holiday not shared across a multi-market panel, a vendor outage) into a
fabricated "0% return" for that bar and a wrong return for the bar after
it. base.py's own NaN policy ("every operator propagates NaN; no silent
fillna(0)") and CONTRIBUTING.md's reviewer checklist ("NaN preserved at
warmup / missing-data positions") both require the gap to stay NaN.
Commit 22867b01 already fixed this identical pattern in
alpha_bench_tool.py's _compute_forward_returns (see
test_alpha_bench_forward_returns.py); this file extends the same fix to
the 13 zoo factors that compute an internal returns feature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research.factors.registry import Registry

# The 13 alphas whose formula depends on an interior close.pct_change()
# gap staying NaN (all fixed by the same one-keyword change).
_AFFECTED_ALPHA_IDS = [
    "academic_bab",
    "alpha101_001",
    "alpha101_008",
    "alpha101_014",
    "alpha101_019",
    "alpha101_025",
    "alpha101_029",
    "alpha101_034",
    "alpha101_035",
    "alpha101_036",
    "alpha101_039",
    "alpha101_052",
    "alpha101_056",
]

N_ROWS = 300
N_SYMS = 3
GAP_ROW = 260  # past every affected alpha's min_warmup_bars (max 253, academic_bab)


def _panel_with_one_gap() -> dict[str, pd.DataFrame]:
    """A synthetic OHLCV+vwap panel with one interior missing close on SYM1,
    the shape every registered alpha's compute(panel) expects."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    cols = [f"SYM{i}" for i in range(N_SYMS)]

    close = (
        pd.DataFrame(
            100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, N_SYMS)), axis=0),
            index=idx,
            columns=cols,
        ).abs()
        + 1.0
    )
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.DataFrame(
        np.maximum(close.to_numpy(), open_.to_numpy()) + rng.uniform(0.0, 1.0, size=(N_ROWS, N_SYMS)),
        index=idx,
        columns=cols,
    )
    low = (
        pd.DataFrame(
            np.minimum(close.to_numpy(), open_.to_numpy()) - rng.uniform(0.0, 1.0, size=(N_ROWS, N_SYMS)),
            index=idx,
            columns=cols,
        ).abs()
        + 0.01
    )
    volume = pd.DataFrame(
        rng.integers(1_000, 100_000, size=(N_ROWS, N_SYMS)).astype(float),
        index=idx,
        columns=cols,
    )
    vwap = (high + low + close + open_) / 4.0

    close.loc[idx[GAP_ROW], "SYM1"] = np.nan

    sectors = ["A", "B", "C"]
    sector_grid = np.array([sectors[i % len(sectors)] for i in range(N_SYMS)], dtype=object)
    sector = pd.DataFrame(np.broadcast_to(sector_grid, close.shape).copy(), index=idx, columns=cols)

    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "vwap": vwap,
        "sector": sector,
    }


@pytest.fixture(scope="module")
def gap_panel() -> dict[str, pd.DataFrame]:
    return _panel_with_one_gap()


@pytest.mark.parametrize("alpha_id", _AFFECTED_ALPHA_IDS)
def test_returns_gap_stays_nan(alpha_id, gap_panel):
    out = Registry().compute(alpha_id, gap_panel)

    assert pd.isna(
        out.iloc[GAP_ROW]["SYM1"]
    ), f"{alpha_id}: the gap bar itself must be NaN, got {out.iloc[GAP_ROW]['SYM1']!r}"
    assert pd.isna(out.iloc[GAP_ROW + 1]["SYM1"]), (
        f"{alpha_id}: the bar right after the gap must be NaN too, " f"got {out.iloc[GAP_ROW + 1]['SYM1']!r}"
    )
