from __future__ import annotations

import json
import warnings

import pandas as pd
import pytest

pytest_plugins = ["tests.test_local_quant_catalog"]


def bars_fixture(days=35, codes=("000001", "600000")):
    rows = []
    for code in codes:
        for i, day in enumerate(pd.bdate_range("2026-01-01", periods=days)):
            price = 10 + i / 100
            rows.append(
                {
                    "date": day,
                    "code": code,
                    "display_name": "测试股票",
                    "open": price,
                    "high": price + 0.05,
                    "low": price - 0.05,
                    "close": price,
                    "pre_close": price - 0.01,
                    "volume": 10000 if code == "000001" else 20000,
                    "amount": 200000,
                }
            )
    return pd.DataFrame(rows)


def request(**kwargs):
    from src.research.local_quant.models import QuantRequest

    return QuantRequest.model_validate(
        {
            "strategy_id": "ma20",
            "start_date": "2026-01-05",
            "end_date": "2026-02-18",
            "screen": {"ma_window": None, "rank_by": "volume", "top_n": 2},
            "max_positions": 2,
            **kwargs,
        }
    )


def strategy(key="ma20", cadence="daily"):
    feature = {"momentum20_top30": "mom20"}.get(key, "above_ma")
    return {"id": key, "cadence": cadence, "feature": feature}


def test_screen_runs_before_strategy_and_shares_one_portfolio_budget():
    from src.research.local_quant.pipeline import build_targets

    bars = bars_fixture()
    features = bars.copy()
    features["above_ma"] = True
    req = request(screen={"ma_window": None, "rank_by": "volume", "top_n": 1})
    targets, decisions = build_targets(bars, features, req, strategy())
    assert targets.iloc[-2]["600000"] == 1
    assert targets.iloc[-2]["000001"] == 0
    assert decisions["code"].unique().tolist() == ["600000"]
    targets, _ = build_targets(bars, features, request(), strategy())
    assert targets.iloc[-2].tolist() == [0.5, 0.5]
    assert targets.sum(axis=1).max() == 1


def test_future_prices_cannot_change_earlier_decisions():
    from src.research.local_quant.pipeline import build_targets

    bars = bars_fixture()
    feature = bars.assign(above_ma=True)
    expected, _ = build_targets(bars, feature, request(screen={"ma_window": 3}), strategy())
    cutoff = pd.Timestamp("2026-02-02")
    changed = bars.copy()
    changed.loc[changed["date"] > cutoff, ["open", "high", "low", "close"]] *= 10
    actual, _ = build_targets(changed, feature, request(screen={"ma_window": 3}), strategy())
    pd.testing.assert_frame_equal(expected.loc[:cutoff], actual.loc[:cutoff])


def test_monthly_selection_carries_without_daily_rescreening():
    from src.research.local_quant.pipeline import build_targets

    bars = bars_fixture(days=40)
    features = bars.assign(mom20=bars["code"].map({"000001": 0.1, "600000": 0.2}))
    req = request(strategy_id="momentum20_top30", end_date="2026-02-25", max_positions=1)
    targets, _ = build_targets(bars, features, req, strategy("momentum20_top30", "monthly"))
    assert targets.loc["2026-01-29"].sum() == 0
    assert targets.loc["2026-01-30", "600000"] == 1
    assert targets.loc["2026-02-03", "600000"] == 1


def test_reading_causal_columns_drops_future_labels(quant_source):
    from src.research.local_quant.pipeline import load_bars

    bars = bars_fixture()
    bars["next_day_close"] = 99999
    bars.to_csv(quant_source / "data/daily_from_minutes.csv", index=False)
    actual, metadata = load_bars(quant_source, request())
    assert "next_day_close" not in actual
    assert metadata["rows"] == 70
    assert len(metadata["sha256"]) == 64


def test_duplicate_symbol_dates_fail_instead_of_changing_selection(quant_source):
    from src.research.local_quant.pipeline import load_bars

    bars = bars_fixture()
    pd.concat([bars, bars.iloc[:1]]).to_csv(quant_source / "data/daily_from_minutes.csv", index=False)
    with pytest.raises(ValueError, match="duplicate"):
        load_bars(quant_source, request())


def prepare_job(quant_source, tmp_path, req, bars=None):
    from src.research.local_quant.catalog import load_strategy

    bars = bars if bars is not None else bars_fixture()
    bars.to_csv(quant_source / "data/daily_from_minutes.csv", index=False)
    run = tmp_path / "run"
    (run / "code").mkdir(parents=True)
    (run / "artifacts").mkdir()
    loaded = load_strategy(quant_source, req.strategy_id)
    (run / "code/signal_engine.py").write_text(loaded["feature_code"])
    job = {"request": req.model_dump(mode="json"), "quant_root": str(quant_source), "strategy": loaded}
    (run / "quant_job.json").write_text(json.dumps(job))
    return run


def test_engine_uses_prior_close_next_open_and_excludes_warmup(quant_source, tmp_path):
    from src.research.local_quant.pipeline import execute_job

    bars = bars_fixture(days=5, codes=("000001",))
    req = request(
        end_date="2026-01-07",
        codes=["000001"],
        initial_cash=10000,
        commission_rate=0,
        commission_min=0,
        stamp_tax=0,
        transfer_fee=0,
        slippage=0,
    )
    run = prepare_job(quant_source, tmp_path, req, bars)
    result = execute_job(run)
    equity = pd.read_csv(run / "artifacts/equity.csv")
    fills = [json.loads(line) for line in (run / "artifacts/fills.jsonl").read_text().splitlines()]
    assert pd.Timestamp(equity["timestamp"].iloc[0]) == pd.Timestamp("2026-01-05")
    assert float(fills[0]["execution_price"]) == pytest.approx(10.02)
    assert pd.Timestamp(fills[0]["timestamp"]) == pd.Timestamp("2026-01-05")
    assert result["metrics"]["final_value"] == pytest.approx(10018)
    assert result["open_positions"] == 1


def test_zero_candidates_produces_cash_only_summary(quant_source, tmp_path):
    from src.research.local_quant.pipeline import execute_job

    req = request(screen={"min_close": 9999, "ma_window": None, "rank_by": "volume"})
    run = prepare_job(quant_source, tmp_path, req)
    result = execute_job(run)
    assert result["metrics"]["total_return"] == 0
    assert result["metrics"]["trade_count"] == 0
    assert result["filled_orders"] == 0
    assert result["candidate_rows"] == 0
    assert (run / "artifacts/summary.md").is_file()


def test_engine_charges_actual_fees_without_selling_final_day_holdings(quant_source, tmp_path):
    from src.research.local_quant.pipeline import execute_job

    req = request(
        end_date="2026-01-07",
        codes=["000001"],
        initial_cash=10000,
        commission_rate=0,
        commission_min=5,
        stamp_tax=0,
        transfer_fee=0,
        slippage=0,
    )
    run = prepare_job(quant_source, tmp_path, req, bars_fixture(days=5, codes=("000001",)))
    result = execute_job(run)
    assert result["metrics"]["final_value"] == pytest.approx(10013)
    assert result["filled_orders"] == 1
    assert result["open_positions"] == 1


def test_missing_later_bar_does_not_remove_stock_from_earlier_candidates():
    from src.research.local_quant.pipeline import build_targets

    bars = bars_fixture(days=40)
    before, _ = build_targets(bars, bars.assign(mom20=0.2), request(), strategy("momentum20_top30", "monthly"))
    cutoff = pd.Timestamp("2026-02-02")
    missing = bars.loc[~((bars["code"] == "600000") & (bars["date"] > cutoff))]
    after, _ = build_targets(missing, missing.assign(mom20=0.2), request(), strategy("momentum20_top30", "monthly"))
    pd.testing.assert_frame_equal(before.loc[:cutoff], after.loc[:cutoff])


def test_suspended_stock_does_not_move_month_end_back_to_last_quote():
    from src.research.local_quant.pipeline import build_targets

    bars = bars_fixture(days=40, codes=("000001",))
    calendar = sorted(bars["date"].unique())
    bars = bars.loc[~bars["date"].between("2026-01-21", "2026-02-09")].copy()
    bars.attrs["market_calendar"] = [str(pd.Timestamp(d).date()) for d in calendar]
    targets, _ = build_targets(bars, bars.assign(mom20=0.2), request(), strategy("momentum20_top30", "monthly"))
    assert targets.loc["2026-01-20"].sum() == 0
    assert pd.Timestamp("2026-01-30") in targets.index


def test_quarantine_is_explicit_and_does_not_repair_zero_prices(quant_source):
    from src.research.local_quant.pipeline import load_bars

    bars = bars_fixture()
    bars.loc[0, "open"] = 0
    bars.to_csv(quant_source / "data/daily_from_minutes.csv", index=False)
    with pytest.raises(ValueError, match="invalid"):
        load_bars(quant_source, request())
    req = request(data_quality="quarantine")
    actual, metadata = load_bars(quant_source, req)
    assert len(actual) == 69
    assert metadata["quarantined_rows"] == 1
    assert not ((actual["code"] == "000001") & (actual["date"] == pd.Timestamp("2026-01-01"))).any()


def test_market_calendar_is_independent_of_requested_codes(quant_source):
    from src.research.local_quant.pipeline import load_bars

    bars = bars_fixture()
    bars = bars.loc[~((bars["code"] == "000001") & (bars["date"] == pd.Timestamp("2026-01-30")))]
    bars.to_csv(quant_source / "data/daily_from_minutes.csv", index=False)
    actual, _ = load_bars(quant_source, request(codes=["000001"]))
    assert "2026-01-30" in actual.attrs["market_calendar"]


def test_captured_source_is_used_after_original_changes(quant_source, tmp_path):
    from src.research.local_quant.pipeline import execute_job

    run = prepare_job(quant_source, tmp_path, request())
    (quant_source / "simple_tactics_backtest.py").write_text("raise RuntimeError('changed after capture')")
    assert execute_job(run)["filled_orders"] > 0


def test_missing_boolean_features_do_not_flood_runner_with_warnings():
    from src.research.local_quant.pipeline import build_targets

    bars = bars_fixture()
    features = bars.assign(above_ma=True).iloc[1:].copy()
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        targets, _ = build_targets(bars, features, request(), strategy())
    assert targets.iloc[0]["000001"] == 0


def read_fills(run):
    return [json.loads(line) for line in (run / "artifacts/fills.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("gap", ["2026-01-05", "2026-01-06"])
def test_missing_sessions_skip_pending_orders_and_preserve_account(quant_source, tmp_path, gap):
    from src.research.local_quant.pipeline import execute_job

    bars = bars_fixture(days=7)
    bars = bars.loc[~((bars["code"] == "000001") & bars["date"].eq(gap))]
    req = request(codes=["000001"], end_date="2026-01-09", initial_cash=10000)
    run = prepare_job(quant_source, tmp_path, req, bars)
    result = execute_job(run)
    fills = read_fills(run)
    assert fills
    assert all(pd.Timestamp(fill["timestamp"]) != pd.Timestamp(gap) for fill in fills)
    assert result["metrics"]["final_value"] > 0
    equity = pd.read_csv(run / "artifacts/equity.csv")
    assert len(equity) == 5
    if gap == "2026-01-06":
        assert equity["equity"].iloc[1] == pytest.approx(equity["equity"].iloc[0])


@pytest.mark.parametrize("pending_exit", [False, True])
def test_zero_volume_sessions_cannot_fill_buys_or_exits(quant_source, tmp_path, pending_exit):
    from src.research.local_quant.pipeline import execute_job

    bars = bars_fixture(days=7, codes=("000001",))
    blocked = "2026-01-06" if pending_exit else "2026-01-05"
    bars.loc[bars["date"].eq(blocked), "volume"] = 0
    req = request(codes=["000001"], end_date="2026-01-09", initial_cash=10000)
    run = prepare_job(quant_source, tmp_path, req, bars)
    if pending_exit:
        (run / "code/signal_engine.py").write_text(
            "import pandas as pd\ndef build_features(frame):\n"
            "    return frame.assign(above_ma=frame['date'].lt(pd.Timestamp('2026-01-05')))\n"
        )
    execute_job(run)
    fills = read_fills(run)
    assert fills
    assert all(pd.Timestamp(fill["timestamp"]) != pd.Timestamp(blocked) for fill in fills)
    if pending_exit:
        assert [pd.Timestamp(fill["timestamp"]) for fill in fills] == [
            pd.Timestamp("2026-01-05"),
            pd.Timestamp("2026-01-07"),
        ]


def test_monthly_strategy_executes_only_at_window_initialization_and_month_roll(quant_source, tmp_path):
    from src.research.local_quant.pipeline import execute_job

    bars = bars_fixture(days=48)
    rising = bars["code"].eq("600000")
    bars.loc[rising, ["open", "high", "low", "close", "pre_close"]] *= (
        1.02 ** bars.loc[rising].groupby("code").cumcount()
    ).to_numpy()[:, None]
    req = request(strategy_id="momentum20_top30", start_date="2026-02-05", end_date="2026-03-06")
    run = prepare_job(quant_source, tmp_path, req, bars)
    execute_job(run)
    dates = {str(pd.Timestamp(fill["timestamp"]).date()) for fill in read_fills(run)}
    assert dates == {"2026-02-05", "2026-03-02"}


@pytest.mark.parametrize("requested_codes", [None, ["000001"]])
def test_cash_only_run_survives_stale_first_symbol_or_entirely_halted_universe(quant_source, tmp_path, requested_codes):
    from src.research.local_quant.pipeline import execute_job

    bars = bars_fixture(days=40)
    bars = bars.loc[~(bars["code"].eq("000001") & bars["date"].gt("2026-01-02"))]
    req = request(
        codes=requested_codes,
        start_date="2026-02-03",
        end_date="2026-02-05",
        screen={"min_close": 9999, "ma_window": None, "rank_by": "volume"},
    )
    run = prepare_job(quant_source, tmp_path, req, bars)
    result = execute_job(run)
    assert result["filled_orders"] == 0
    assert result["metrics"]["final_value"] == req.initial_cash
    assert result["metrics"]["total_return"] == 0
    assert len(pd.read_csv(run / "artifacts/equity.csv")) == 3
