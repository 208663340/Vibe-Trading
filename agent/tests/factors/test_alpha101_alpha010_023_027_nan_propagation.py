"""Regression: alpha101_010, alpha101_023 and alpha101_027 must not
fabricate a signal before their declared warmup, and must propagate NaN
through a gap.

All three use where_ternary chains whose branches stay finite well
before the real lookback is available (alpha_010's fallback only needs
a 1-day delta; alpha_023's is 0.0 times a finite close; alpha_027's
branches are pure constants). A NaN comparison evaluates False, not
NaN, so where_ternary always falls through instead of propagating NaN.
Same bug class already fixed in
alpha101_007/009/021/024/046/049/051.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.registry import Registry


def _panel_2sym(n_rows: int, seed: int = 11):
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    rng = np.random.default_rng(seed)
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(n_rows, 2)), axis=0),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    high = close + 1.0
    return idx, close, high


def test_alpha010_no_signal_before_declared_warmup():
    _, close, _ = _panel_2sym(30)
    out = Registry().compute("alpha101_010", {"close": close})

    warmup = out["SYM0"].iloc[:4]
    assert warmup.isna().all(), (
        f"alpha101_010: rows before the 4-day lookback must stay NaN, "
        f"got {warmup.tolist()}"
    )
    assert pd.notna(out["SYM0"].iloc[4])


def test_alpha010_gap_stays_nan():
    idx, close, _ = _panel_2sym(30)
    close.loc[idx[15], "SYM1"] = np.nan
    out = Registry().compute("alpha101_010", {"close": close})

    assert pd.isna(out["SYM1"].iloc[15]), (
        f"alpha101_010: the gap row itself must stay NaN, "
        f"got {out['SYM1'].iloc[15]!r}"
    )
    unaffected = out["SYM0"].iloc[4:]
    assert (
        not unaffected.isna().any()
    ), "alpha101_010: a symbol with no gap must not pick up stray NaN"


def test_alpha023_no_signal_before_declared_warmup():
    _, close, high = _panel_2sym(30)
    out = Registry().compute("alpha101_023", {"close": close, "high": high})

    warmup = out["SYM0"].iloc[:19]
    assert warmup.isna().all(), (
        f"alpha101_023: rows before the 20-day lookback must stay NaN, "
        f"got {warmup.tolist()}"
    )
    assert pd.notna(out["SYM0"].iloc[19])


def test_alpha023_gap_stays_nan():
    idx, close, high = _panel_2sym(30)
    close.loc[idx[15], "SYM1"] = np.nan
    high.loc[idx[15], "SYM1"] = np.nan
    out = Registry().compute("alpha101_023", {"close": close, "high": high})

    assert pd.isna(out["SYM1"].iloc[15]), (
        f"alpha101_023: the gap row itself must stay NaN, "
        f"got {out['SYM1'].iloc[15]!r}"
    )
    unaffected = out["SYM0"].iloc[19:]
    assert (
        not unaffected.isna().any()
    ), "alpha101_023: a symbol with no gap must not pick up stray NaN"


def _panel_6sym(n_rows: int, seed: int = 11):
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    rng = np.random.default_rng(seed)
    cols = [f"SYM{i}" for i in range(6)]
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(n_rows, 6)), axis=0),
        index=idx,
        columns=cols,
    )
    high = close + 1.0
    volume = pd.DataFrame(
        rng.integers(1_000, 2_000, size=(n_rows, 6)).astype(float),
        index=idx,
        columns=cols,
    )
    vwap = (close + high) / 2.0
    return idx, close, volume, vwap


def test_alpha027_no_signal_before_declared_warmup():
    # rank() needs more than 2 columns to produce non-degenerate
    # correlation input for ts_corr, so this alpha needs a wider panel.
    _, close, volume, vwap = _panel_6sym(30)
    out = Registry().compute(
        "alpha101_027", {"close": close, "volume": volume, "vwap": vwap}
    )

    warmup = out["SYM0"].iloc[:6]
    assert warmup.isna().all(), (
        f"alpha101_027: rows before ts_corr's 6-day lookback must stay "
        f"NaN, got {warmup.tolist()}"
    )
