"""Agent tools for trusted local quant strategy discovery and portfolio tests."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any

from src.platform.config.accessor import get_env_config
from src.platform.config.paths import get_runs_dir
from src.platform.core.runner import Runner
from src.platform.core.state import RunStateStore
from src.research.local_quant.catalog import list_strategies, load_strategy, quant_root
from src.research.local_quant.models import QuantRequest
from src.runtime.agent.progress import emit_progress
from src.runtime.agent.tools import BaseTool
from src.runtime.tools.path_utils import safe_run_dir


def _envelope(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class ListLocalQuantStrategiesTool(BaseTool):
    name = "list_local_quant_strategies"
    description = (
        "Discover the operator's local quant strategies before choosing one. "
        "Returns source, decision cadence and whether close-only backtesting is supported. "
        "For local quant research: list, load a suitable strategy, then backtest_local_quant_strategy."
    )
    parameters = {"type": "object", "properties": {}, "additionalProperties": False}
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            kwargs.pop("run_dir", None)  # Agent/Swarm injects its workspace, outside the model schema.
            if kwargs:
                raise ValueError("list_local_quant_strategies takes no arguments")
            root = quant_root()
            return _envelope(
                {
                    "status": "ok",
                    "strategies": list_strategies(root),
                    "quant_root": str(root),
                    "configuration": "Operator setting: VIBE_TRADING_QUANT_ROOT",
                }
            )
        except (ValueError, OSError) as exc:
            return _envelope({"status": "error", "error": str(exc)})


class LoadLocalQuantStrategyTool(BaseTool):
    name = "load_local_quant_strategy"
    description = (
        "Load one named local quant strategy's actual feature expressions, source hash and cadence. "
        "Use this instead of inventing a replacement strategy. Top-level script code is not executed. "
        "Then call backtest_local_quant_strategy with a close-time screen and evaluation dates."
    )
    parameters = {
        "type": "object",
        "properties": {"strategy_id": {"type": "string"}},
        "required": ["strategy_id"],
        "additionalProperties": False,
    }
    repeatable = True

    def execute(self, **kwargs: Any) -> str:
        try:
            kwargs.pop("run_dir", None)
            if set(kwargs) != {"strategy_id"} or not isinstance(kwargs["strategy_id"], str):
                raise ValueError("load_local_quant_strategy requires only strategy_id")
            loaded = load_strategy(quant_root(), kwargs["strategy_id"])
            preview = loaded.pop("feature_code")
            loaded.pop("source_files")
            return _envelope(
                {
                    "status": "ok",
                    "strategy": loaded,
                    "feature_code": preview[:12000],
                    "feature_code_truncated": len(preview) > 12000,
                    "execution": "Close-time screen, then native shared-cash A-share backtest; next-session-open fills. "
                    "Monthly rules retain their target basket until the next month-end decision. "
                    "The supported universe is main-board stocks. Historical source-script bookkeeping is replaced by native accounting.",
                }
            )
        except (ValueError, OSError, SyntaxError) as exc:
            return _envelope({"status": "error", "error": str(exc)})


def _validated_summary(run_dir: Path) -> dict:
    path = run_dir / "artifacts/summary.json"
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise ValueError("backtest did not produce a complete summary.json")
    summary = json.loads(path.read_text(encoding="utf-8"))
    metrics = summary["metrics"]
    for key in ["final_value", "total_return", "annual_return", "max_drawdown", "sharpe", "win_rate", "trade_count"]:
        value = metrics[key]
        if key == "annual_return" and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError(f"backtest metric {key} must be finite")
    if metrics["final_value"] < 0 or metrics["trade_count"] < 0:
        raise ValueError("backtest produced invalid capital/trade count")
    for name in [
        "candidates.csv",
        "metrics.csv",
        "positions.csv",
        "trades.csv",
        "fills.jsonl",
        "summary.md",
        "excluded_bars.csv",
        "execution_skips.csv",
    ]:
        if not (run_dir / "artifacts" / name).is_file():
            raise ValueError(f"backtest did not produce {name}")
    with (run_dir / "artifacts/equity.csv").open(newline="", encoding="utf-8-sig") as handle:
        last = None
        for row in csv.DictReader(handle):
            last = row
    if last is None or not math.isclose(float(last["equity"]), metrics["final_value"], rel_tol=1e-6, abs_tol=0.01):
        raise ValueError("equity.csv and portfolio summary disagree")
    return summary


class BacktestLocalQuantStrategyTool(BaseTool):
    name = "backtest_local_quant_strategy"
    description = (
        "Screen local stocks using completed daily K-lines, apply a loaded local quant strategy "
        "inside the candidate basket, and run one shared-cash portfolio with the native A-share engine. "
        "List/load first; choose dates, optional main-board codes, screen thresholds, top_n and max_positions. "
        "No future labels or live orders. Returns actual net performance, candidate/fill evidence and a Chinese report. "
        "screen.min_momentum20 uses fractions; turnover thresholds use the CSV's percentage-point scale. "
        "Defaults: close above MA20, top 50 by 20-day momentum, at most 10 target holdings."
        " Invalid bars fail by default; explicitly set data_quality='quarantine' to exclude and audit them without repairing prices."
    )
    parameters = QuantRequest.model_json_schema()
    repeatable = True
    is_readonly = False

    def execute(self, **kwargs: Any) -> str:
        run_dir = None
        state = RunStateStore()
        try:
            context_dir = kwargs.pop("run_dir", None)
            workspace = safe_run_dir(str(context_dir)) if context_dir else None
            request = QuantRequest.model_validate(kwargs)
            root = quant_root()
            loaded = load_strategy(root, request.strategy_id)
            emit_progress("validate", message="capturing local strategy and close-time screen")
            run_dir = state.create_run_dir(get_runs_dir())
            (run_dir / "code/signal_engine.py").write_text(loaded["feature_code"], encoding="utf-8")
            snapshot = run_dir / "code/source"
            snapshot.mkdir()
            for name, source in loaded["source_files"].items():
                (snapshot / name).write_text(source, encoding="utf-8")
            job = {"quant_root": str(root), "request": request.model_dump(mode="json"), "strategy": loaded}
            (run_dir / "quant_job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
            state.save_request(
                run_dir, f"本地量化策略 {loaded['title']}：收盘筛选与组合回测", request.model_dump(mode="json")
            )
            (run_dir / "user_prompt.txt").write_text(f"本地量化策略：{loaded['title']}", encoding="utf-8")
            initial_config = {
                "source": "local",
                "engine": "china_a",
                "mode": "local_quant",
                "interval": "1D",
                "start_date": str(request.start_date),
                "end_date": str(request.end_date),
                "initial_cash": request.initial_cash,
                "codes": request.codes or [],
            }
            (run_dir / "config.json").write_text(json.dumps(initial_config), encoding="utf-8")
            timeout = get_env_config().agent_tuning.vibe_trading_tool_timeout_seconds
            timeout = min(float(timeout), 900) if float(timeout) > 0 else 900
            runner = Runner(timeout=timeout)
            script = Path(__file__).resolve().parents[2] / "research/local_quant/runner.py"
            emit_progress("simulate", message=f"screening and backtesting {loaded['title']}")
            result = runner.execute(script, run_dir, cwd=Path(__file__).resolve().parents[3], cli_args=[str(run_dir)])
            if not result.success:
                raise ValueError(f"local quant backtest failed (exit {result.exit_code}): {result.stderr[-2000:]}")
            summary = _validated_summary(run_dir)
            state.mark_success(run_dir)
            emit_progress("finalize", message="collecting portfolio metrics and backtest report")
            names = [
                "summary.md",
                "summary.json",
                "candidates.csv",
                "equity.csv",
                "trades.csv",
                "positions.csv",
                "metrics.csv",
                "fills.jsonl",
                "excluded_bars.csv",
                "execution_skips.csv",
            ]
            artifact_dir = run_dir / "artifacts"
            if workspace is not None:
                # File tools are confined to the invoking agent's workspace.
                # Publish a distinct evidence bundle there while retaining the
                # native application run for the Reports API and provenance.
                bundle = (workspace / "artifacts/local_quant" / run_dir.name).resolve()
                if not bundle.is_relative_to(workspace):
                    raise ValueError("local quant evidence bundle escapes the agent workspace")
                bundle.mkdir(parents=True, exist_ok=False)
                for name in names:
                    shutil.copyfile(artifact_dir / name, bundle / name)
                artifact_dir = bundle
            return _envelope(
                {
                    "status": "ok",
                    "run_id": run_dir.name,
                    "run_dir": str(run_dir),
                    "strategy_id": request.strategy_id,
                    "metrics": summary["metrics"],
                    "quarantined_rows": summary["dataset"]["quarantined_rows"],
                    **{
                        key: summary[key]
                        for key in [
                            "effective_start",
                            "effective_end",
                            "candidate_rows",
                            "candidate_stocks",
                            "selected_decisions",
                            "filled_orders",
                            "open_positions",
                            "unavailable_execution_decisions",
                        ]
                    },
                    "artifacts": {name: str(artifact_dir / name) for name in names},
                    "limitations": summary["limitations"],
                }
            )
        except subprocess.TimeoutExpired as exc:
            if run_dir is not None:
                for name, value in [("stdout", exc.stdout), ("stderr", exc.stderr)]:
                    text = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
                    (run_dir / "logs" / f"runner_{name}.txt").write_text(text, encoding="utf-8")
                state.mark_failure(run_dir, "local quant backtest timed out")
            return _envelope(
                {
                    "status": "error",
                    "error": "local quant backtest timed out",
                    "run_dir": str(run_dir) if run_dir else None,
                }
            )
        except Exception as exc:
            if run_dir is not None:
                state.mark_failure(run_dir, str(exc))
            return _envelope(
                {"status": "error", "error": str(exc)[:3000], "run_dir": str(run_dir) if run_dir else None}
            )
