"""Execute a captured experiment using the existing backtest runner."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from src.backtest.program_contract import load_program_metrics, program_args, resolve_entrypoint
from src.backtest.run_card import write_run_card
from src.platform.core.runner import Runner
from src.research.experiments.service import read_json, write_json


def execute(run: Path) -> int:
    try:
        config = read_json(run / "config.json")
        manifest = read_json(run / "experiment.json")
        if config["mode"] == "program":
            entry = resolve_entrypoint(config)
            cwd = run / "source"
            args = program_args(config)
        else:
            entry = Path(__file__).resolve().parents[2] / "backtest" / "runner.py"
            cwd = Path(__file__).resolve().parents[3]
            args = [str(run)]
        outcome = Runner(timeout=manifest["timeout_seconds"]).execute(entry, run, cwd=cwd, cli_args=args)
        if not outcome.success:
            raise ValueError(f"Program exited with code {outcome.exit_code}; inspect logs")
        metrics = load_program_metrics(run)
        card = write_run_card(run, config, metrics, data_sources=[str(config.get("source", "local"))], strategy_path=entry if config["mode"] == "program" else run / "code" / "signal_engine.py")
        card["experiment"] = {"run_id": manifest["run_id"], "project": manifest["project"], "parent_run_id": manifest.get("parent_run_id"), "execution_source": manifest["execution_source"], "snapshot_scope": "explicit_files", "files": manifest["files"]}
        card["checks"] = {"artifact_schema": "passed", "accounting_reconciliation": "not_run", "out_of_sample": "not_run"}
        write_json(run / "run_card.json", card)
        write_json(run / "execution.json", {"status": "success", "metrics": metrics, "checks": card["checks"]})
        return 0
    except Exception as exc:
        traceback.print_exc()
        write_json(run / "execution.json", {"status": "failed", "reason": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(execute(Path(sys.argv[1]).resolve()))
