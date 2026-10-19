import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def quant_source(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "simple_long_backtest.py").write_text(
        'PREFIXES = ("000", "600")\nTOP_N = 50\n'
        "def load_bars():\n"
        "    frame = pd.read_csv(DAILY)\n"
        '    frame["ma60"] = frame.groupby("code")["close"].transform(lambda x: x.rolling(2).mean())\n'
        '    frame["mom60"] = frame.groupby("code")["close"].pct_change(2)\n'
        '    frame["vol60"] = frame.groupby("code")["close"].pct_change().abs()\n'
        "    return frame\n",
        encoding="utf-8",
    )
    (tmp_path / "simple_tactics_backtest.py").write_text(
        'raise RuntimeError("top level must never execute")\n'
        'MAIN = ("000", "600")\n'
        "def load():\n"
        "    frame = pd.read_csv(DAILY)\n"
        '    frame["ma20"] = frame.groupby("code")["close"].transform(lambda x: x.rolling(2).mean())\n'
        '    frame["above_ma"] = frame["close"] > frame["ma20"]\n'
        '    frame["mom20"] = frame.groupby("code")["close"].pct_change(2)\n'
        '    frame["next_open"] = frame.groupby("code")["open"].shift(-1)\n'
        '    frame["oo"] = frame["next_open"] / frame["open"] - 1\n'
        '    frame["limit_up"] = frame["close"] >= frame["pre_close"] * 1.098\n'
        '    frame["turtle"] = turtle_position(frame)\n'
        "    return frame\n"
        "def turtle_position(frame):\n"
        '    return frame["above_ma"].to_numpy()\n',
        encoding="utf-8",
    )
    return tmp_path


def test_discovery_and_load_do_not_execute_script_top_level(quant_source):
    from src.research.local_quant.catalog import list_strategies, load_strategy

    rows = {x["id"]: x for x in list_strategies(quant_source)}
    assert rows["ma20"]["available"]
    assert not rows["board_overnight"]["close_only_supported"]
    loaded = load_strategy(quant_source, "ma20")
    assert loaded["source_files"]["simple_tactics_backtest.py"].startswith("raise RuntimeError")
    assert len(loaded["source_sha256"]["simple_tactics_backtest.py"]) == 64


def test_feature_adapter_uses_source_expression_and_removes_forward_returns(quant_source):
    from src.research.local_quant.catalog import load_strategy

    frame = pd.DataFrame(
        {"code": ["000001"] * 3, "close": [10, 12, 11], "open": [10, 11, 12], "pre_close": [10, 10, 12]}
    )
    namespace = {"pd": pd, "np": np}
    loaded = load_strategy(quant_source, "ma20")
    exec(loaded["feature_code"], namespace)
    actual = namespace["build_features"](frame)
    assert actual["ma20"].tolist()[1:] == [11, 11.5]
    assert actual["above_ma"].tolist() == [False, True, False]
    assert "next_open" not in actual
    assert "oo" not in actual
    path = quant_source / "simple_tactics_backtest.py"
    path.write_text(path.read_text().replace("rolling(2)", "rolling(3)"))
    changed = load_strategy(quant_source, "ma20")
    exec(changed["feature_code"], namespace)
    assert namespace["build_features"](frame)["ma20"].tolist()[-1] == 11
    assert changed["source_sha256"] != loaded["source_sha256"]


@pytest.mark.parametrize("strategy_id", ["../ma20", "arbitrary.py", "unknown"])
def test_unknown_strategy_cannot_select_a_python_file(quant_source, strategy_id):
    from src.research.local_quant.catalog import load_strategy

    with pytest.raises(ValueError, match="strategy"):
        load_strategy(quant_source, strategy_id)


def test_source_symlink_outside_trusted_root_is_refused(quant_source, tmp_path_factory):
    from src.research.local_quant.catalog import load_strategy

    outside = tmp_path_factory.mktemp("other") / "source.py"
    outside.write_text("raise RuntimeError('not a strategy')")
    path = quant_source / "simple_tactics_backtest.py"
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ValueError, match="root"):
        load_strategy(quant_source, "ma20")


@pytest.mark.parametrize(
    "expression",
    [
        'frame["close"].shift(-1)',
        'frame["next_day_close"]',
        'frame["close"].bfill()',
        'frame["close"].rolling(3, center=True).mean()',
    ],
)
def test_future_features_in_source_are_refused(quant_source, expression):
    from src.research.local_quant.catalog import load_strategy

    path = quant_source / "simple_tactics_backtest.py"
    path.write_text(path.read_text().replace('frame["close"] > frame["ma20"]', expression))
    with pytest.raises(ValueError, match="causal|future"):
        load_strategy(quant_source, "ma20")


@pytest.mark.parametrize(
    "patch",
    [
        {"start_date": "2026-10-31", "end_date": "2026-01-01"},
        {"initial_cash": float("nan")},
        {"initial_cash": True},
        {"max_positions": 0},
        {"codes": ["../secret"]},
        {"screen": {"ma_window": 0}},
        {"screen": {"rank_by": "next_day_return"}},
        {"source_path": "/tmp/other.csv"},
        {"data_quality": "repair_with_future_prices"},
    ],
)
def test_invalid_backtest_request_is_rejected(patch):
    from src.research.local_quant.models import QuantRequest

    args = {"strategy_id": "ma20", "start_date": "2026-01-01", "end_date": "2026-10-14", **patch}
    with pytest.raises(ValueError):
        QuantRequest.model_validate(args)


def test_operator_config_selects_root_without_model_path_argument(monkeypatch, tmp_path):
    from src.platform.config.accessor import reset_env_config
    from src.research.local_quant.catalog import quant_root

    monkeypatch.setenv("VIBE_TRADING_QUANT_ROOT", str(tmp_path))
    reset_env_config()
    assert quant_root() == tmp_path.resolve()
