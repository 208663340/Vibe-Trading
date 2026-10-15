"""Regression: gtja191_019 and gtja191_086 must propagate NaN through
warmup and data gaps instead of fabricating a value.

Both alphas pick a fallback branch via nested np.where comparisons fed
by a delayed close. A NaN comparison evaluates False, not NaN, so a
missing close or delayed close (warmup, or a halt) reads exactly like
the fallback condition and returns a fabricated number instead of NaN.
Same bug class already fixed in gtja191_003/004/059/069.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.registry import Registry

N_ROWS = 55
GAP_ROW = 30


def _panel_with_one_gap() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(3)
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    cols = ["SYM0", "SYM1"]

    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, 2)), axis=0),
        index=idx,
        columns=cols,
    )
    close.loc[idx[GAP_ROW], "SYM1"] = np.nan
    return {"close": close}


def test_alpha019_no_signal_before_declared_warmup():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_019", panel)

    warmup = out["SYM0"].iloc[:5]
    assert warmup.isna().all(), (
        f"gtja191_019: rows before the 5-day lookback must stay NaN, "
        f"got {warmup.tolist()}"
    )
    assert pd.notna(out["SYM0"].iloc[5])


def test_alpha019_gap_stays_nan():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_019", panel)

    for row in (GAP_ROW, GAP_ROW + 5):
        assert pd.isna(out["SYM1"].iloc[row]), (
            f"gtja191_019: row {row} touching the gap must stay NaN, "
            f"got {out['SYM1'].iloc[row]!r}"
        )
    unaffected = out["SYM0"].iloc[6:]
    assert (
        not unaffected.isna().any()
    ), "gtja191_019: a symbol with no gap must not pick up stray NaN"


def test_alpha086_no_signal_before_declared_warmup():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_086", panel)

    warmup = out["SYM0"].iloc[:20]
    assert warmup.isna().all(), (
        f"gtja191_086: rows before the 20-day lookback must stay NaN, "
        f"got {warmup.tolist()}"
    )
    assert pd.notna(out["SYM0"].iloc[20])


def test_alpha086_gap_stays_nan():
    panel = _panel_with_one_gap()
    out = Registry().compute("gtja191_086", panel)

    for row in (GAP_ROW, GAP_ROW + 10, GAP_ROW + 20):
        assert pd.isna(out["SYM1"].iloc[row]), (
            f"gtja191_086: row {row} touching the gap must stay NaN, "
            f"got {out['SYM1'].iloc[row]!r}"
        )
    unaffected = out["SYM0"].iloc[21:]
    assert (
        not unaffected.isna().any()
    ), "gtja191_086: a symbol with no gap must not pick up stray NaN"
