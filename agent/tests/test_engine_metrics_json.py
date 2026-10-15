"""Strict JSON for BaseEngine scalar metrics stdout."""

from __future__ import annotations

import json

import pandas as pd

import src.backtest.engines.base as base_mod
from src.backtest.metrics import calc_metrics


def test_engine_scalar_metrics_stdout_is_strict_json() -> None:
    """Explosive equity sets annual_return=inf; stdout must not emit Infinity."""
    eq = pd.Series([1.0, 1_000_000.0, 500_000.0])
    m = calc_metrics(eq, [], 1.0, bars_per_year=525_600)
    assert m["annual_return"] == float("inf")

    scrub = getattr(base_mod, "_json_safe_scalar_metrics", None)
    if scrub is None:
        # Pristine engine printed scalars with default allow_nan=True.
        payload = {k: v for k, v in m.items() if not isinstance(v, dict)}
    else:
        payload = scrub(m)

    raw = json.dumps(payload, allow_nan=False)
    assert "Infinity" not in raw and "NaN" not in raw
    loaded = json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    assert loaded["annual_return"] is None
    assert loaded["total_return"] == m["total_return"]
