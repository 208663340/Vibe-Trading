"""Regression: gtja191_004 must propagate NaN through a data gap, not
fabricate a -1.0 signal.

compute() picks a branch with nested np.where on comparisons like
``upper < ma2``. A NaN comparison evaluates False, not NaN, so when any
of the underlying rolling stats (ma8/sd8/ma2/vmean20) is NaN because a
trading halt sits inside its lookback window, every comparison falls
through to the innermost branch and returns a hard -1.0 instead of NaN.
base.py's own NaN policy ("every operator propagates NaN; no silent
fillna") requires the gap to stay NaN, and it must stay NaN for every
bar whose window still touches the gap, not just the gap bar itself.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.registry import Registry

N_ROWS = 60
GAP_ROW = 30  # past min_warmup_bars=20


def _panel_with_one_gap() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    cols = ["SYM0", "SYM1"]

    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, 2)), axis=0),
        index=idx,
        columns=cols,
    )
    volume = pd.DataFrame(
        rng.integers(1_000, 100_000, size=(N_ROWS, 2)).astype(float),
        index=idx,
        columns=cols,
    )

    close.loc[idx[GAP_ROW], "SYM1"] = np.nan
    volume.loc[idx[GAP_ROW], "SYM1"] = np.nan

    return {"close": close, "volume": volume}


def test_gap_stays_nan_through_the_full_affected_window():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_004", panel)

    # The gap poisons every rolling window (up to 8/20 bars) still
    # touching it, not just the gap row.
    affected = out["SYM1"].iloc[GAP_ROW : GAP_ROW + 8]
    assert affected.isna().all(), (
        f"gtja191_004: every bar whose 8-day window touches the gap must "
        f"stay NaN, got {affected.tolist()}"
    )


def test_unaffected_symbol_still_computes_real_values():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_004", panel)

    unaffected = out["SYM0"].iloc[20:]
    assert not unaffected.isna().any(), (
        "gtja191_004: a symbol with no gap must not pick up stray NaN "
        "from another symbol's column"
    )
    assert set(unaffected.unique()) <= {-1.0, 1.0}
