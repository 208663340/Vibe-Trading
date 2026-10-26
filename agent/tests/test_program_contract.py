"""Custom program runs must stay inside program_root and emit the report CSVs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

import pytest

from src.backtest.program_contract import load_program_metrics, resolve_entrypoint
from src.runtime.tools.backtest_tool import run_backtest


def test_entrypoint_rejects_paths_outside_program_root(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "secret.py"
    outside.write_text("print(1)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="program_root"):
        resolve_entrypoint({"entrypoint": str(outside), "program_root": str(root)})


def test_metrics_loader_names_a_missing_column(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "equity.csv").write_text("timestamp,equity,ret,drawdown\n2024-01-01,1,0,0\n", encoding="utf-8")
    (artifacts / "trades.csv").write_text(
        "timestamp,code,side,pnl,reason,return_pct\n2024-01-01,000001,sell,-1,rule,-1\n",
        encoding="utf-8",
    )
    (artifacts / "metrics.csv").write_text("final_value\n1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="metrics.csv missing columns"):
        load_program_metrics(tmp_path)


@dataclass
class _FakeRunResult:
    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    artifacts: dict = field(default_factory=dict)


def _write_contract(run_dir: Path) -> None:
    artifacts = run_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "equity.csv").write_text(
        "timestamp,equity,ret,drawdown\n2024-01-01,100,0,0\n2024-02-01,80,-0.2,-0.2\n",
        encoding="utf-8",
    )
    (artifacts / "trades.csv").write_text(
        "timestamp,code,side,pnl,reason,return_pct\n2024-02-01,000001,sell,-20,ma60,-20\n",
        encoding="utf-8",
    )
    (artifacts / "metrics.csv").write_text(
        "final_value,total_return,annual_return,sharpe,max_drawdown,win_rate,trade_count\n"
        "80,-0.2,-0.2,-1,-0.2,0,1\n",
        encoding="utf-8",
    )


def test_program_mode_writes_run_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "strategy.py").write_text("print('ok')\n", encoding="utf-8")
    run_dir = tmp_path / "run-program"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(
        json.dumps(
            {
                "mode": "program",
                "entrypoint": "strategy.py",
                "program_root": str(workspace),
                "start_date": "2024-01-01",
                "end_date": "2024-02-01",
                "initial_cash": 100,
            }
        ),
        encoding="utf-8",
    )

    def execute(entry_script, run_path, cwd=None, cli_args=None):
        assert entry_script.name == "strategy.py"
        assert cwd == workspace
        _write_contract(run_path)
        return _FakeRunResult(True, 0, stdout="ok")

    with patch("src.runtime.tools.backtest_tool.emit_progress"), patch(
        "src.runtime.tools.backtest_tool.Runner"
    ) as runner_cls:
        runner_cls.return_value.execute.side_effect = execute
        envelope = json.loads(run_backtest(str(run_dir)))

    assert envelope["status"] == "ok"
    card = json.loads((run_dir / "run_card.json").read_text(encoding="utf-8"))
    assert card["backtest"]["engine"] == "program"
    assert card["metrics"]["total_return"] == -0.2
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "success"


def test_program_mode_fails_when_artifacts_are_incomplete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "strategy.py").write_text("print('ok')\n", encoding="utf-8")
    run_dir = tmp_path / "run-program"
    run_dir.mkdir()
    (run_dir / "config.json").write_text(
        json.dumps({"mode": "program", "entrypoint": "strategy.py", "program_root": str(workspace)}),
        encoding="utf-8",
    )

    with patch("src.runtime.tools.backtest_tool.emit_progress"), patch(
        "src.runtime.tools.backtest_tool.Runner"
    ) as runner_cls:
        runner_cls.return_value.execute.return_value = _FakeRunResult(True, 0)
        envelope = json.loads(run_backtest(str(run_dir)))

    assert envelope["status"] == "error"
    assert "missing equity.csv" in envelope["error"]
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed"
