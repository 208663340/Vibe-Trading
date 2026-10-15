from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


_TEMPLATE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "research"
    / "skills"
    / "cross-market-strategy"
    / "example_signal_engine.py"
)


def _load_signal_engine():
    spec = spec_from_file_location("cross_market_strategy_template", _TEMPLATE_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SignalEngine


def test_cross_market_volatility_weights_preserve_missing_price_gaps():
    index = pd.date_range("2026-01-01", periods=7)
    gapped = pd.DataFrame(
        {
            "close": [
                100.0,
                91.99768809729663,
                84.9885742607376,
                80.4613602033837,
                77.0077833662644,
                np.nan,
                101.00351323123887,
            ]
        },
        index=index,
    )
    peer = pd.DataFrame(
        {"close": [100.0, 105.0, 100.0, 104.0, 99.0, 103.0, 98.0]},
        index=index,
    )
    signals = {
        "AAPL.US": pd.Series(0.5, index=index),
        "BTC-USDT": pd.Series(0.5, index=index),
    }

    adjusted = _load_signal_engine()()._vol_adjust(
        signals,
        {"AAPL.US": gapped, "BTC-USDT": peer},
    )

    gapped_vol = gapped["close"].pct_change(fill_method=None).dropna().std()
    peer_vol = peer["close"].pct_change(fill_method=None).dropna().std()
    expected_weight = (
        (1.0 / (gapped_vol + 1e-10))
        / ((1.0 / (gapped_vol + 1e-10)) + (1.0 / (peer_vol + 1e-10)))
        * 2
    )

    assert adjusted["AAPL.US"].iloc[-1] == pytest.approx(0.5 * expected_weight)
