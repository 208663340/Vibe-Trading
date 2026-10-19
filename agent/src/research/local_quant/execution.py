"""Local panel execution: missing and zero-volume sessions cannot trade."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.engines.china_a import ChinaAEngine


class LocalQuantEngine(ChinaAEngine):
    def __init__(self, config):
        super().__init__(config)
        self.skipped_orders = []

    def _execute_target_rebalance(self, target_weights, data_map, ts, equity, codes):
        executable = dict(target_weights)
        for symbol in codes:
            frame = data_map.get(symbol)
            bar = frame.loc[ts] if frame is not None and ts in frame.index else None
            reason = None
            if bar is None or not np.isfinite(bar[["open", "high", "low", "close"]]).all():
                reason = "missing_bar"
            elif not np.isfinite(bar["volume"]) or bar["volume"] <= 0:
                reason = "zero_volume"
            if reason:
                if self.positions.get(symbol) is not None or abs(target_weights.get(symbol) or 0) > 1e-9:
                    self.skipped_orders.append({"date": ts, "code": symbol, "reason": reason})
                executable[symbol] = None
        super()._execute_target_rebalance(executable, data_map, ts, equity, codes)


def monthly_execution_dates(calendar: pd.DatetimeIndex, evaluated: pd.DatetimeIndex) -> list[str]:
    """Initialize at the first evaluation open, then execute after each month-end close.

    An unfilled monthly order expires that session; it is not silently retried
    between decisions. Daily strategies instead evaluate a new target each close.
    """
    rolls = calendar[1:][calendar[:-1].to_period("M") != calendar[1:].to_period("M")]
    dates = {evaluated[0], *(day for day in rolls if evaluated[0] <= day <= evaluated[-1])}
    return [str(day.date()) for day in sorted(dates)]
