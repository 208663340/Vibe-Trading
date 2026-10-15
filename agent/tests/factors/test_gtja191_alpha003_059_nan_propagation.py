"""Regression: gtja191_003 and gtja191_059 must propagate NaN through a
data gap instead of fabricating a 0.0 "unchanged" move.

Both alphas compute a signed daily move that is 0.0 on a real tie
(close == prior close) and otherwise close minus a reference price, then
sum it over a rolling window. compute() picks the tie branch with
``(c - ref).where(up | dn, 0.0)`` where ``up``/``dn`` are comparisons
against the prior close. A NaN comparison evaluates False, not NaN, so a
missing close or prior close (a halt, a gap) reads exactly like a real
tie and gets the fabricated 0.0 instead of NaN — the same class of bug
already fixed in gtja191_004. base.py's own NaN policy ("every operator
propagates NaN; no silent fillna") requires the gap to stay NaN through
every window that still touches it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research.factors.registry import Registry

N_ROWS = 60
GAP_ROW = 20  # past both alphas' min_warmup_bars (7 and 22)


def _panel_with_one_gap() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(1)
    idx = pd.date_range("2024-01-01", periods=N_ROWS, freq="D")
    cols = ["SYM0", "SYM1"]

    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(N_ROWS, 2)), axis=0),
        index=idx,
        columns=cols,
    )
    high = close + 1.0
    low = close - 1.0

    close.loc[idx[GAP_ROW], "SYM1"] = np.nan
    high.loc[idx[GAP_ROW], "SYM1"] = np.nan
    low.loc[idx[GAP_ROW], "SYM1"] = np.nan

    return {"close": close, "high": high, "low": low}


@pytest.mark.parametrize(
    "alpha_id, window",
    [("gtja191_003", 6), ("gtja191_059", 20)],
)
def test_gap_stays_nan_through_the_full_affected_window(alpha_id, window):
    panel = _panel_with_one_gap()
    out = Registry().compute(alpha_id, panel)

    # The gap poisons the move on GAP_ROW (missing close) and GAP_ROW + 1
    # (missing prior close), so up to window + 1 rolling windows still
    # touch it before real data alone can fill min_periods again.
    affected = out["SYM1"].iloc[GAP_ROW : GAP_ROW + window + 1]
    assert affected.isna().all(), (
        f"{alpha_id}: every window still touching the gap must stay NaN, "
        f"got {affected.tolist()}"
    )

    # And it must actually clear once the gap scrolls out of the window.
    after = out["SYM1"].iloc[GAP_ROW + window + 1]
    assert not pd.isna(
        after
    ), f"{alpha_id}: NaN must not leak past the gap's own window"


@pytest.mark.parametrize("alpha_id", ["gtja191_003", "gtja191_059"])
def test_unaffected_symbol_still_computes_real_values(alpha_id):
    panel = _panel_with_one_gap()
    out = Registry().compute(alpha_id, panel)

    unaffected = out["SYM0"].iloc[25:]
    assert not unaffected.isna().any(), (
        f"{alpha_id}: a symbol with no gap must not pick up stray NaN "
        f"from another symbol's column"
    )
