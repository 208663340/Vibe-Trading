"""Regression tests for the correlation-analysis skill examples."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


SKILL_MD = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "research"
    / "skills"
    / "correlation-analysis"
    / "SKILL.md"
)


def _engle_granger_code() -> str:
    text = SKILL_MD.read_text(encoding="utf-8")
    section = text.split("### Engle-Granger Two-Step Method", 1)[1]
    return section.split("```python\n", 1)[1].split("```", 1)[0]


def _load_engle_granger():
    namespace = {"pd": pd}
    exec(_engle_granger_code(), namespace)
    return namespace["engle_granger_coint"]


@pytest.mark.parametrize("name", [None, "price_x"])
def test_engle_granger_handles_series_name(name):
    pytest.importorskip("statsmodels", reason="the optional 'statsmodels' package is not installed")
    rng = np.random.default_rng(42)
    x = pd.Series(np.cumsum(rng.normal(size=250)), name=name)
    y = 1.7 * x + pd.Series(rng.normal(scale=0.2, size=250))

    result = _load_engle_granger()(y, x)

    assert result["hedge_ratio"] == pytest.approx(1.7, abs=0.02)
    assert result["is_cointegrated"] is True


def test_engle_granger_example_uses_quantlib_not_a_local_fit():
    """The unnamed-series KeyError lived in a hand-rolled OLS fit inside the
    skill; the tested implementation is ``src.research.quantlib.timeseries``."""
    code = _engle_granger_code()

    assert "from src.research.quantlib.timeseries import" in code
    assert "sm.OLS" not in code
