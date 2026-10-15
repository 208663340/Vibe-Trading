from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import numpy as np
import pandas as pd


_TEMPLATE_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "research"
    / "skills"
    / "strategy-dev-manager"
    / "templates"
    / "strategy_signal_engine.py"
)


def _load_signal_engine():
    spec = spec_from_file_location("strategy_signal_engine_template", _TEMPLATE_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.SignalEngine


def test_strategy_template_preserves_missing_price_gaps():
    df = pd.DataFrame(
        {"close": [80.0, 80.0, 90.0, np.nan, 80.0]},
        index=pd.date_range("2026-01-01", periods=5),
    )

    signal = _load_signal_engine()(lookback=2).generate({"TEST": df})["TEST"]

    assert signal.iloc[-1] == 0.0
