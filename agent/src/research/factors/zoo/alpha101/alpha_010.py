
# ============================================================
# 中文名称: Alpha #10 - 连续价格变动
# 简要说明: rank((0 < ts_min(delta(close, 1), 4)) ? delta(close, 1) : ((ts_max(delta(close, 1), 4) < 0) ? delta(close, 1) : (-1 * delta(close, 1))))，类似Alpha #9的4日版本。
# 典型用途: 短期价格变动的趋势或反转判断，用于短线交易。
# ============================================================
"""Kakushadze Alpha #10.

Formula (paper appendix): rank((0<ts_min(delta(close,1),4))?delta(close,1):((ts_max(delta(close,1),4)<0)?delta(close,1):(-1*delta(close,1))))
Source: Kakushadze (2015), "101 Formulaic Alphas", arXiv:1601.00991, eq. 10.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.research.factors.base import (
    decay_linear,
    delta,
    rank,
    safe_div,
    scale,
    signed_power,
    ts_argmax,
    ts_argmin,
    ts_corr,
    ts_cov,
    ts_max,
    ts_mean,
    ts_min,
    ts_rank,
    ts_std,
)

ALPHA_ID = "alpha101_010"

__alpha_meta__ = {
    'id': 'alpha101_010',
    'nickname': 'Kakushadze Alpha #10',
    'theme': ['momentum'],
    'formula_latex': 'rank((0<ts_min(delta(close,1),4))?delta(close,1):((ts_max(delta(close,1),4)<0)?delta(close,1):(-1*delta(close,1))))',
    'columns_required': ['close'],
    'extras_required': [],
    'requires_sector': False,
    'universe': ['equity_us', 'equity_in', 'equity_kr'],
    'frequency': ['1D'],
    'decay_horizon': 5,
    'min_warmup_bars': 5,
    'notes': '',
}


def _where_ternary(cond, a, b):
    """Vectorised ternary `(cond ? a : b)` returning a DataFrame.

    ``cond`` is a boolean DataFrame; ``a`` / ``b`` may be DataFrame or scalar.
    """
    if isinstance(a, (int, float)):
        a_arr = np.full_like(cond.to_numpy(dtype=np.float64), float(a))
    else:
        a_arr = a.to_numpy(dtype=np.float64, na_value=np.nan)
    if isinstance(b, (int, float)):
        b_arr = np.full_like(cond.to_numpy(dtype=np.float64), float(b))
    else:
        b_arr = b.to_numpy(dtype=np.float64, na_value=np.nan)
    cond_arr = cond.to_numpy(dtype=bool, na_value=False) if hasattr(cond, "to_numpy") else np.asarray(cond, dtype=bool)
    out = np.where(cond_arr, a_arr, b_arr)
    out = np.where(np.isfinite(out), out, np.nan)
    idx = cond.index if hasattr(cond, "index") else a.index
    cols = cond.columns if hasattr(cond, "columns") else a.columns
    return pd.DataFrame(out, index=idx, columns=cols)


def compute(panel: dict) -> pd.DataFrame:
    """Compute the alpha on the OHLCV+ panel and return a wide DataFrame."""
    close = panel["close"]


    # Helper aliases (local closures keep the file standalone & purity-safe).
    where_ternary = _where_ternary
    d1 = delta(close, 1)
    min4 = ts_min(d1, 4)
    max4 = ts_max(d1, 4)
    cond1 = min4 > 0
    cond2 = max4 < 0
    inner = where_ternary(cond1, d1, where_ternary(cond2, d1, -1.0 * d1))
    # A NaN comparison is False, not NaN, so where_ternary's own
    # np.isfinite safety net never fires here: the innermost fallback
    # only needs a 1-day delta and stays finite well before the 4-day
    # lookback is available, fabricating a signal during warmup instead
    # of NaN. rank() preserves NaN, so masking inner is enough.
    inner = inner.where(min4.notna() & max4.notna())
    out = rank(inner)
    return out
