"""A China-futures run must say which numbers it assumed (#1393).

``china_futures.py`` keeps four product tables of different sizes. A product
listed in one and absent from another does not fail the run -- it silently
takes a generic constant (margin 0.10, commission ``("fixed", 5.0)``,
multiplier 10, price limit 0.05) that is indistinguishable from a real table
entry in the output. ``pg`` is the case #1389's own test used to prove its fix:
its multiplier is now correct and its margin rate is still the default, so the
same contract is half-looked-up and half-assumed.

The direction agreed on #1393 is to make the default observable rather than to
fill the tables, because filling them leaves the *next* added product silently
defaulting. Numbers are unchanged; the run now states which of them were
assumed.

The sharp case is ``rb``: its real margin rate IS 0.10, the same number as the
default. A report that cannot tell "looked up 0.10" from "assumed 0.10" would
be worthless, so that discrimination is asserted directly.
"""

from __future__ import annotations

import logging

import pytest

from src.backtest.engines.china_futures import (
    _COMMISSION,
    _DEFAULT_COMMISSION,
    _DEFAULT_MARGIN_RATE,
    _DEFAULT_MULTIPLIER,
    _MARGIN_RATE,
    _MULTIPLIER,
    ChinaFuturesEngine,
)


def _engine(*codes: str) -> ChinaFuturesEngine:
    return ChinaFuturesEngine({"codes": list(codes), "initial_capital": 1_000_000})


def _assumptions(engine: ChinaFuturesEngine) -> dict[tuple[str, str], object]:
    reported = engine._engine_diagnostics().get("pricing_assumptions", [])
    return {(row["product"], row["field"]): row["value"] for row in reported}


class TestPricingAssumptionsAreReported:
    def test_a_product_missing_from_a_table_is_reported(self) -> None:
        engine = _engine("pg2412.DCE")
        engine.get_margin_rate("pg2412.DCE")
        engine.calc_commission_for_symbol("pg2412.DCE", 1, 4000.0, True)

        assumed = _assumptions(engine)
        assert assumed[("pg", "margin_rate")] == _DEFAULT_MARGIN_RATE
        assert assumed[("pg", "commission")] == _DEFAULT_COMMISSION

    def test_the_value_the_run_used_is_unchanged(self) -> None:
        """Observability only: every number stays exactly what it was."""
        engine = _engine("pg2412.DCE")
        assert engine.get_margin_rate("pg2412.DCE") == _DEFAULT_MARGIN_RATE
        assert engine.get_contract_multiplier("pg2412.DCE") == float(
            _MULTIPLIER["pg"]
        )
        assert engine.calc_commission_for_symbol("pg2412.DCE", 2, 4000.0, True) == 10.0

    def test_a_real_entry_equal_to_the_default_is_not_reported(self) -> None:
        """``rb``'s looked-up margin is 0.10 -- the default's own value."""
        assert _MARGIN_RATE["rb"] == _DEFAULT_MARGIN_RATE
        engine = _engine("rb2410.SHFE")
        assert engine.get_margin_rate("rb2410.SHFE") == _DEFAULT_MARGIN_RATE

        assert ("rb", "margin_rate") not in _assumptions(engine)

    def test_a_fully_covered_run_declares_nothing(self) -> None:
        engine = _engine("IF2406.CFFEX")
        engine.get_margin_rate("IF2406.CFFEX")
        engine.get_contract_multiplier("IF2406.CFFEX")
        engine.calc_commission_for_symbol("IF2406.CFFEX", 1, 3800.0, True)

        assert engine._engine_diagnostics() == {}

    def test_pg_is_the_half_real_contract_the_issue_names(self) -> None:
        """#1389 fixed pg's multiplier; its margin rate is still assumed."""
        assert "pg" in _MULTIPLIER and _MULTIPLIER["pg"] != _DEFAULT_MULTIPLIER
        assert "pg" not in _MARGIN_RATE
        assert "pg" not in _COMMISSION

        engine = _engine("pg2412.DCE")
        engine.get_contract_multiplier("pg2412.DCE")
        engine.get_margin_rate("pg2412.DCE")

        assumed = _assumptions(engine)
        assert ("pg", "contract_multiplier") not in assumed
        assert ("pg", "margin_rate") in assumed

    def test_the_warning_fires_once_per_field_not_once_per_bar(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        engine = _engine("pg2412.DCE")
        with caplog.at_level(logging.WARNING, logger="src.backtest.engines.china_futures"):
            for _ in range(50):
                engine.get_margin_rate("pg2412.DCE")

        margin_warnings = [
            record
            for record in caplog.records
            if "margin_rate" in record.getMessage()
        ]
        assert len(margin_warnings) == 1
        assert "pg" in margin_warnings[0].getMessage()

    def test_leverage_derived_at_construction_is_also_reported(self) -> None:
        """__init__ prices leverage off codes[0] before any bar is seen."""
        engine = _engine("pg2412.DCE")

        assert ("pg", "margin_rate") in _assumptions(engine)
        assert engine._leverage_for_symbol("pg2412.DCE") == 1.0 / _DEFAULT_MARGIN_RATE


class TestDiagnosticsSeam:
    def test_base_engine_declares_nothing_by_default(self) -> None:
        from src.backtest.engines.china_a import ChinaAEngine

        assert ChinaAEngine({"initial_capital": 1_000_000})._engine_diagnostics() == {}

    def test_the_report_reaches_the_run_metrics_not_just_the_hook(
        self, tmp_path
    ) -> None:
        """A hook nobody merges into the result is the failure this guards."""
        import numpy as np
        import pandas as pd

        index = pd.date_range("2026-01-01", periods=60, freq="D")
        prices = np.linspace(4000.0, 4400.0, 60)
        frame = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.01,
                "low": prices * 0.99,
                "close": prices,
                "volume": 1e5,
                "pre_settle": np.r_[prices[0], prices[:-1]],
            },
            index=index,
        )

        class _Loader:
            def fetch(self, *args, **kwargs):
                return {"pg2412.DCE": frame}

        class _Signals:
            def generate(self, data_map):
                return {"pg2412.DCE": pd.Series(1.0, index=index)}

        config = {
            "codes": ["pg2412.DCE"],
            "initial_capital": 1_000_000,
            "start_date": "2026-01-01",
            "end_date": "2026-03-01",
        }
        result = ChinaFuturesEngine(config).run_backtest(
            config, _Loader(), _Signals(), tmp_path
        )

        metrics = result.get("metrics", result)
        reported = {
            (row["product"], row["field"]) for row in metrics["pricing_assumptions"]
        }
        assert ("pg", "margin_rate") in reported
        assert ("pg", "commission") in reported
        # pg's multiplier is a real table entry (#1389), so it is not assumed.
        assert ("pg", "contract_multiplier") not in reported
