"""Regression: alpha101_049 must not fabricate a signal before its
declared warmup, and must propagate NaN through a data gap.

compute() picks a ternary via where_ternary(x < -0.1, 1, else_branch).
x needs delay(close, 20), so it is NaN for the first 20 rows. A NaN
comparison evaluates False, not NaN, so where_ternary always falls to
else_branch there, which only needs delay(close, 1) and is finite from
row 1 onward. The alpha's own np.isfinite safety net only catches a
non-finite output, not an output computed from an undefined condition,
so it does not fire. min_warmup_bars=21 declares the first 20 rows
unreliable, but compute() returned real-looking numbers for 19 of them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.registry import Registry

N_ROWS = 55
GAP_ROW = 25  # past min_warmup_bars=21, leaves room for +20 lookback


def test_no_signal_before_declared_warmup():
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    rng = np.random.default_rng(1)
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, 2)), axis=0),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    out = Registry().compute("alpha101_049", {"close": close})

    # x needs delay(close, 20): rows 0-19 (before the 21st bar) must stay
    # NaN, not a fabricated -1*(close-delay(close,1)) reading.
    warmup = out.iloc[:20]
    assert warmup.isna().all().all(), (
        f"alpha101_049: rows before min_warmup_bars must stay NaN, "
        f"got {warmup.stack().tolist()}"
    )
    assert (
        out.iloc[20:].notna().any().any()
    ), "alpha101_049: must produce real values once warmed up"


def test_gap_stays_nan_not_fabricated():
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    rng = np.random.default_rng(1)
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, 2)), axis=0),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    close.loc[idx[GAP_ROW], "SYM1"] = np.nan
    out = Registry().compute("alpha101_049", {"close": close})

    # The gap feeds x directly at the gap row, and via delay(10)/delay(20)
    # ten and twenty rows later.
    for offset in (0, 10, 20):
        row = GAP_ROW + offset
        assert pd.isna(out["SYM1"].iloc[row]), (
            f"alpha101_049: row {row} (gap + {offset}) must stay NaN, "
            f"got {out['SYM1'].iloc[row]!r}"
        )

    unaffected = out["SYM0"].iloc[20:]
    assert not unaffected.isna().any(), (
        "alpha101_049: a symbol with no gap must not pick up stray NaN "
        "from another symbol's column"
    )
