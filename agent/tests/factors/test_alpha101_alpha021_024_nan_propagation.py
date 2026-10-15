"""Regression: alpha101_021 and alpha101_024 must not fabricate a signal
before their declared warmup, and must propagate NaN through a gap.

Both use where_ternary chains whose branches are all finite well before
the real lookback is available (alpha_021's branches are pure constants;
alpha_024's else branch only needs a 3-day delta against a 200-day
condition). A NaN comparison evaluates False, not NaN, so the chain
always falls through instead of propagating NaN. Same bug class already
fixed in alpha101_007/009/046/049/051.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.registry import Registry


def test_alpha021_no_signal_before_declared_warmup():
    n_rows = 25
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    rng = np.random.default_rng(8)
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(n_rows, 2)), axis=0),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    volume = pd.DataFrame(
        rng.integers(1_000, 2_000, size=(n_rows, 2)).astype(float),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    out = Registry().compute("alpha101_021", {"close": close, "volume": volume})

    # adv20 needs 20 observations: rows 0-18 must stay NaN, not one of
    # the fully-constant fallback branches.
    warmup = out.iloc[:19]
    assert warmup.isna().all().all(), (
        f"alpha101_021: rows before the 20-day lookback must stay NaN, "
        f"got {warmup.stack().tolist()}"
    )
    assert out.iloc[19:].notna().any().any()


def test_alpha021_gap_stays_nan():
    n_rows = 50
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    rng = np.random.default_rng(8)
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(n_rows, 2)), axis=0),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    volume = pd.DataFrame(
        rng.integers(1_000, 2_000, size=(n_rows, 2)).astype(float),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    close.loc[idx[30], "SYM1"] = np.nan
    out = Registry().compute("alpha101_021", {"close": close, "volume": volume})

    assert pd.isna(out["SYM1"].iloc[30]), (
        f"alpha101_021: the gap row itself must stay NaN, "
        f"got {out['SYM1'].iloc[30]!r}"
    )
    unaffected = out["SYM0"].iloc[19:]
    assert (
        not unaffected.isna().any()
    ), "alpha101_021: a symbol with no gap must not pick up stray NaN"


def test_alpha024_no_signal_before_declared_warmup():
    n_rows = 210
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    rng = np.random.default_rng(9)
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(n_rows, 2)), axis=0),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    out = Registry().compute("alpha101_024", {"close": close})

    # x needs delta(m100, 100): rows before index 199 must stay NaN, not
    # the else branch's fabricated 3-day delta.
    warmup = out.iloc[:199]
    assert (
        warmup.isna().all().all()
    ), "alpha101_024: rows before the 200-day lookback must stay NaN"
    assert pd.notna(out["SYM0"].iloc[199])


def test_alpha024_gap_stays_nan():
    n_rows = 220
    idx = pd.date_range("2024-01-01", periods=n_rows, freq="D")
    rng = np.random.default_rng(9)
    close = pd.DataFrame(
        100.0 + np.cumsum(rng.normal(0.0, 1.0, size=(n_rows, 2)), axis=0),
        index=idx,
        columns=["SYM0", "SYM1"],
    )
    close.loc[idx[210], "SYM1"] = np.nan
    out = Registry().compute("alpha101_024", {"close": close})

    assert pd.isna(out["SYM1"].iloc[210]), (
        f"alpha101_024: the gap row itself must stay NaN, "
        f"got {out['SYM1'].iloc[210]!r}"
    )
    unaffected = out["SYM0"].iloc[199:]
    assert (
        not unaffected.isna().any()
    ), "alpha101_024: a symbol with no gap must not pick up stray NaN"
