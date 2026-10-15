from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import pandas as pd


_TEMPLATE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "research"
    / "skills"
    / "multi-factor"
    / "example_signal_engine.py"
)


def _load_signal_engine():
    spec = spec_from_file_location("multi_factor_strategy_template", _TEMPLATE_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SignalEngine


def test_multi_factor_excludes_assets_without_factor_observations():
    index = pd.date_range("2026-01-01", periods=8)
    data_map = {
        "A": pd.DataFrame(
            {
                "close": [100, 102, 104, 106, 108, 110, 112, 114],
                "volume": [100, 110, 120, 130, 140, 150, 160, 170],
            },
            index=index,
        ),
        "B": pd.DataFrame(
            {
                "close": [100, 99, 98, 97, 96, 95, 94, 93],
                "volume": [100, 90, 80, 70, 60, 50, 40, 30],
            },
            index=index,
        ),
        "MISSING": pd.DataFrame(
            {"close": [np.nan] * 8, "volume": [np.nan] * 8},
            index=index,
        ),
    }

    signals = _load_signal_engine()(
        momentum_window=2,
        vol_window=2,
        top_n=2,
        rebalance_freq=1,
    ).generate(data_map)

    assert signals["A"].iloc[-1] == 0.5
    assert signals["B"].iloc[-1] == 0.5
    assert signals["MISSING"].iloc[-1] == 0.0
