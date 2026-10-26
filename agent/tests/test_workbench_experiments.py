"""Real local programs exercise the standalone experiment lifecycle."""
from __future__ import annotations

import importlib
import json
import time
from pathlib import Path

import pytest


PROGRAM = '''import os
from pathlib import Path
out = Path(os.environ["RUN_DIR"]) / "artifacts"
out.mkdir(exist_ok=True)
(out / "equity.csv").write_text("timestamp,equity,ret,drawdown\\n2024-01-01,100,0,0\\n2024-01-02,100,0,0\\n")
(out / "trades.csv").write_text("timestamp,code,side,pnl,reason,return_pct\\n")
(out / "metrics.csv").write_text("final_value,total_return,annual_return,sharpe,max_drawdown,win_rate,trade_count\\n100,0,0,0,0,0,0\\n")
'''


def service_type():
    name = "src.research.experiments.service"
    try:
        module = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        pytest.fail(f"Independent experiment service is missing: {exc}")
    return module.ExperimentService


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / "workspace"
    source.mkdir()
    (source / "strategy.py").write_text(PROGRAM)
    return source


def wait_done(service, run_id, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = service.get(run_id)
        if item["status"] not in {"queued", "running", "cancelling"}:
            return item
        time.sleep(0.03)
    pytest.fail(f"experiment did not finish: {service.get(run_id)}")


def submit(service, workspace, **overrides):
    return service.submit({"source_dir": str(workspace), "name": "Cash baseline", "project": "daily", "files": ["strategy.py"], "config": {"mode": "program", "entrypoint": "strategy.py"}, **overrides})


def test_program_snapshots_source_and_preserves_zero_trade_report(tmp_path, workspace):
    with service_type()(tmp_path / "state", workspace) as service:
        item = submit(service, workspace)
        (workspace / "strategy.py").write_text("raise RuntimeError('changed after submission')")
        result = wait_done(service, item["run_id"])
        assert result["status"] == "success", result
        run = tmp_path / "state" / "runs" / item["run_id"]
        assert (run / "source" / "strategy.py").read_text() == PROGRAM
        assert json.loads((run / "run_card.json").read_text())["metrics"]["trade_count"] == 0
        assert result["project"] == "daily"
        assert result["execution_source"] == "program"
        assert result["files"][0]["sha256"]
    with service_type()(tmp_path / "state", workspace) as reopened:
        assert reopened.get(item["run_id"])["status"] == "success"


def test_failure_retains_logs_and_parent_experiment(tmp_path, workspace):
    with service_type()(tmp_path / "state", workspace) as service:
        parent = submit(service, workspace)
        assert wait_done(service, parent["run_id"])["status"] == "success"
        (workspace / "strategy.py").write_text("raise RuntimeError('deliberate failure')")
        child = submit(service, workspace, parent_run_id=parent["run_id"])
        failed = wait_done(service, child["run_id"])
        assert failed["status"] == "failed"
        assert failed["parent_run_id"] == parent["run_id"]
        run = tmp_path / "state" / "runs" / child["run_id"]
        assert "deliberate failure" in (run / "logs" / "worker.log").read_text()


def test_cancel_waits_for_owned_process_before_terminal_state(tmp_path, workspace):
    (workspace / "strategy.py").write_text("import time\nprint('started', flush=True)\ntime.sleep(120)\n")
    with service_type()(tmp_path / "state", workspace) as service:
        item = submit(service, workspace)
        deadline = time.monotonic() + 5
        while service.get(item["run_id"])["status"] == "queued" and time.monotonic() < deadline:
            time.sleep(0.02)
        service.cancel(item["run_id"])
        assert wait_done(service, item["run_id"])["status"] == "cancelled"


@pytest.mark.parametrize("files", [["../outside.py"], ["strategy.py", ".env"], ["escape.py"]])
def test_snapshot_rejects_escapes_and_credential_files(tmp_path, workspace, files):
    (tmp_path / "outside.py").write_text("print('outside')")
    (workspace / ".env").write_text("NOT_A_REAL_SECRET=fixture")
    (workspace / "escape.py").symlink_to(tmp_path / "outside.py")
    with service_type()(tmp_path / "state", workspace) as service:
        with pytest.raises(ValueError):
            submit(service, workspace, files=files)
        assert service.list() == []


def test_restart_marks_unfinished_record_interrupted_and_preserves_old_runs(tmp_path, workspace):
    root = tmp_path / "state"
    run = root / "runs" / "unfinished"
    run.mkdir(parents=True)
    (run / "experiment.json").write_text(json.dumps({"run_id": "unfinished", "name": "unfinished", "status": "running"}))
    (run / "state.json").write_text('{"status":"running"}')
    legacy = root / "runs" / "legacy"
    legacy.mkdir()
    (legacy / "state.json").write_text('{"status":"success"}')
    with service_type()(root, workspace) as service:
        assert service.get("unfinished")["status"] == "interrupted"
        assert json.loads((legacy / "state.json").read_text())["status"] == "success"


def test_second_service_cannot_take_over_running_owner(tmp_path, workspace):
    with service_type()(tmp_path / "state", workspace):
        with pytest.raises(RuntimeError, match="already"):
            service_type()(tmp_path / "state", workspace).start()


def test_nonfinite_program_metrics_are_rejected(tmp_path):
    from src.backtest.program_contract import load_program_metrics
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "equity.csv").write_text("timestamp,equity,ret,drawdown\n2024-01-01,100,0,0\n")
    (artifacts / "trades.csv").write_text("timestamp,code,side,pnl,reason,return_pct\n2024-01-01,X,sell,0,flat,0\n")
    (artifacts / "metrics.csv").write_text("final_value,total_return,annual_return,sharpe,max_drawdown,win_rate,trade_count\ninf,0,0,0,0,0,1\n")
    with pytest.raises(ValueError, match="finite"):
        load_program_metrics(tmp_path)


def test_builtin_worker_reuses_engine_and_emits_report_contract(tmp_path, monkeypatch):
    import pandas as pd
    from types import SimpleNamespace
    from src.backtest import runner
    from src.research.experiments import worker
    from src.research.experiments.service import write_json
    run = tmp_path / "builtin"
    (run / "code").mkdir(parents=True)
    (run / "code" / "signal_engine.py").write_text('import pandas as pd\nclass SignalEngine:\n    def generate(self, data_map):\n        return {code: pd.Series(0.0, index=data.index) for code, data in data_map.items()}\n')
    config = {"mode": "builtin", "source": "local", "codes": ["AAPL.US"], "start_date": "2024-01-01", "end_date": "2024-01-05", "initial_cash": 1000, "benchmark": None}
    write_json(run / "config.json", config)
    write_json(run / "experiment.json", {"run_id":"builtin","project":"test","execution_source":"builtin","timeout_seconds":10,"files":[]})
    data = pd.DataFrame({"open":[100]*5,"high":[101]*5,"low":[99]*5,"close":[100]*5,"volume":[10000]*5}, index=pd.date_range("2024-01-01", periods=5))
    loader = SimpleNamespace(name="local",fetch=lambda *a, **kw: {"AAPL.US": data})
    monkeypatch.setenv("VIBE_TRADING_ALLOWED_RUN_ROOTS", str(tmp_path))
    monkeypatch.setattr(runner,"fetch_data_map",lambda config: runner.DataFetchResult({"AAPL.US":data},["AAPL.US"],"local",loader,["local"]))
    def execute(_self, entry, run_dir, **kwargs):
        runner.main(run_dir)
        return SimpleNamespace(success=True,exit_code=0)
    monkeypatch.setattr(worker.Runner,"execute",execute)
    assert worker.execute(run) == 0
    result=json.loads((run / "execution.json").read_text())
    assert result["status"] == "success"
    assert result["metrics"]["final_value"] == 1000


def test_timeout_is_terminal_failure_with_reason(tmp_path, workspace):
    (workspace / "strategy.py").write_text("import time\ntime.sleep(120)\n")
    with service_type()(tmp_path / "state", workspace) as service:
        item=submit(service,workspace,timeout_seconds=1)
        result=wait_done(service,item["run_id"])
        assert result["status"]=="failed"
        assert "timed out" in result["reason"]


@pytest.mark.skipif(__import__('os').name == 'nt', reason='POSIX process group regression')
@pytest.mark.parametrize('action', ['cancel', 'timeout', 'shutdown'])
def test_cancel_stops_strategy_even_when_worker_exits_before_it(tmp_path, workspace, action):
    import os
    import signal
    (workspace / "strategy.py").write_text('import signal,os,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n(Path(os.environ["RUN_DIR"])/"artifacts"/"pid").write_text(str(os.getpid()))\ntime.sleep(120)\n')
    pid=None
    try:
        with service_type()(tmp_path / "state", workspace) as service:
            item=submit(service,workspace,timeout_seconds=3)
            marker=service.path(item["run_id"])/"artifacts"/"pid"
            deadline=time.monotonic()+10
            while not marker.exists() and time.monotonic()<deadline: time.sleep(0.02)
            assert marker.exists()
            pid=int(marker.read_text())
            if action == "cancel": service.cancel(item["run_id"])
            elif action == "shutdown": service.close()
            expected = {"cancel":"cancelled", "timeout":"failed", "shutdown":"interrupted"}[action]
            assert wait_done(service,item["run_id"])["status"]==expected
            deadline=time.monotonic()+1
            while time.monotonic()<deadline:
                try: os.kill(pid,0)
                except ProcessLookupError: return
                time.sleep(0.02)
            pytest.fail("Strategy remains alive after cancellation became terminal")
    finally:
        if pid:
            try: os.kill(pid,signal.SIGKILL)
            except ProcessLookupError: pass
