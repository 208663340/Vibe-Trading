"""Regression: gtja191_069 must propagate NaN through a data gap instead
of fabricating a 0.0 "tied" signal.

compute() builds sd/sb as 20-day rolling sums that correctly go NaN
across a gap, but the final np.where(sd > sb, ..., np.where(sd < sb,
..., 0.0)) falls through to the tie branch's hard-coded 0.0 whenever
sd or sb is NaN, since a NaN comparison evaluates False, not NaN. Same
bug class already fixed in gtja191_003/004/059.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.registry import Registry

N_ROWS = 60
GAP_ROW = 30  # past min_warmup_bars=22
WINDOW = 20


def _panel_with_one_gap() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(2)
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    cols = ["SYM0", "SYM1"]

    close = 100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, 2)), axis=0)
    open_ = pd.DataFrame(
        close + rng.normal(0.0, 0.1, size=(N_ROWS, 2)), index=idx, columns=cols
    )
    high = open_ + 1.0
    low = open_ - 1.0

    open_.loc[idx[GAP_ROW], "SYM1"] = np.nan
    high.loc[idx[GAP_ROW], "SYM1"] = np.nan
    low.loc[idx[GAP_ROW], "SYM1"] = np.nan

    return {"open": open_, "high": high, "low": low}


def test_gap_stays_nan_through_the_full_affected_window():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_069", panel)

    affected = out["SYM1"].iloc[GAP_ROW + 1 : GAP_ROW + WINDOW + 1]
    assert affected.isna().all(), (
        f"gtja191_069: every window still touching the gap must stay NaN, "
        f"got {affected.tolist()}"
    )

    after = out["SYM1"].iloc[GAP_ROW + WINDOW + 1]
    assert not pd.isna(
        after
    ), "gtja191_069: NaN must not leak past the gap's own window"


def test_unaffected_symbol_still_computes_real_values():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_069", panel)

    unaffected = out["SYM0"].iloc[25:]
    assert not unaffected.isna().any(), (
        "gtja191_069: a symbol with no gap must not pick up stray NaN "
        "from another symbol's column"
    )
