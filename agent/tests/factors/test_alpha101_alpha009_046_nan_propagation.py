"""Regression: alpha101_009 and alpha101_046 must not fabricate a signal
before their declared warmup, and must propagate NaN through a gap.

Both use where_ternary(cond, a, b) chains where cond is a comparison
against a rolling or delay-based intermediate that is NaN during warmup.
A NaN comparison evaluates False, not NaN, so where_ternary always falls
through to a fallback branch that stays finite well before the real
lookback is available. Same bug class already fixed in
alpha101_007/049/051.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.registry import Registry

N_ROWS = 30
GAP_ROW = 15


def _panel_with_one_gap() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(7)
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    cols = ["SYM0", "SYM1"]

    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, 2)), axis=0),
        index=idx,
        columns=cols,
    )
    close.loc[idx[GAP_ROW], "SYM1"] = np.nan
    return {"close": close}


def test_alpha009_no_signal_before_declared_warmup():
    panel = _panel_with_one_gap()
    out = Registry().compute("alpha101_009", panel)

    warmup = out["SYM0"].iloc[:5]
    assert warmup.isna().all(), (
        f"alpha101_009: rows before the 5-day lookback must stay NaN, "
        f"got {warmup.tolist()}"
    )
    assert pd.notna(out["SYM0"].iloc[5])


def test_alpha009_gap_stays_nan():
    panel = _panel_with_one_gap()
    out = Registry().compute("alpha101_009", panel)

    for row in (GAP_ROW, GAP_ROW + 1):
        assert pd.isna(out["SYM1"].iloc[row]), (
            f"alpha101_009: row {row} touching the gap must stay NaN, "
            f"got {out['SYM1'].iloc[row]!r}"
        )
    unaffected = out["SYM0"].iloc[6:]
    assert (
        not unaffected.isna().any()
    ), "alpha101_009: a symbol with no gap must not pick up stray NaN"


def test_alpha046_no_signal_before_declared_warmup():
    panel = _panel_with_one_gap()
    out = Registry().compute("alpha101_046", panel)

    warmup = out["SYM0"].iloc[:20]
    assert warmup.isna().all(), (
        f"alpha101_046: rows before the 20-day lookback must stay NaN, "
        f"got {warmup.tolist()}"
    )
    assert pd.notna(out["SYM0"].iloc[20])


def test_alpha046_gap_stays_nan():
    panel = _panel_with_one_gap()
    out = Registry().compute("alpha101_046", panel)

    assert pd.isna(out["SYM1"].iloc[GAP_ROW]), (
        f"alpha101_046: the gap row itself must stay NaN, "
        f"got {out['SYM1'].iloc[GAP_ROW]!r}"
    )
    unaffected = out["SYM0"].iloc[21:]
    assert (
        not unaffected.isna().any()
    ), "alpha101_046: a symbol with no gap must not pick up stray NaN"
