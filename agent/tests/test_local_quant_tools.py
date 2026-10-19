from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_local_quant_pipeline import bars_fixture

pytest_plugins = ["tests.test_local_quant_catalog"]


@pytest.fixture
def tool_environment(quant_source, tmp_path, monkeypatch):
    from src.platform.config.accessor import reset_env_config

    bars_fixture().to_csv(quant_source / "data/daily_from_minutes.csv", index=False)
    monkeypatch.setenv("VIBE_TRADING_QUANT_ROOT", str(quant_source))
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("VIBE_TRADING_HOME", str(runtime))
    reset_env_config()
    return quant_source, runtime


def tool_args():
    return {
        "strategy_id": "ma20",
        "start_date": "2026-01-05",
        "end_date": "2026-02-18",
        "screen": {"ma_window": None, "rank_by": "volume"},
    }


def test_agent_can_discover_load_and_run_without_shell_tools(tool_environment):
    from src.runtime.tools import build_swarm_registry

    names = ["list_local_quant_strategies", "load_local_quant_strategy", "backtest_local_quant_strategy"]
    registry = build_swarm_registry(names)
    listed = json.loads(registry.execute(names[0], {}))
    assert listed["status"] == "ok"
    assert any(row["id"] == "ma20" for row in listed["strategies"])
    loaded = json.loads(registry.execute(names[1], {"strategy_id": "ma20"}))
    assert loaded["status"] == "ok"
    assert "source_sha256" in loaded["strategy"]
    backtest = registry.get(names[2])
    assert backtest.is_readonly is False
    result = json.loads(registry.execute(names[2], tool_args()))
    assert result["status"] == "ok", result
    run = Path(result["run_dir"])
    assert json.loads((run / "state.json").read_text())["status"] == "success"
    assert Path(result["artifacts"]["summary.md"]).is_file()
    assert Path(result["artifacts"]["fills.jsonl"]).is_file()
    assert result["metrics"]["final_value"] > 0


def test_rejected_request_does_not_create_a_run(tool_environment):
    from src.runtime.tools.local_quant_strategy_tool import BacktestLocalQuantStrategyTool

    root, runtime = tool_environment
    result = json.loads(BacktestLocalQuantStrategyTool().execute(**{**tool_args(), "initial_cash": -1}))
    assert result["status"] == "error"
    assert not (runtime / "runs").exists()


def test_timeout_is_failed_and_keeps_partial_logs(tool_environment):
    from src.runtime.tools.local_quant_strategy_tool import BacktestLocalQuantStrategyTool

    timeout = subprocess.TimeoutExpired("local_quant", 1, output="partial progress", stderr="partial diagnostic")
    with patch("src.runtime.tools.local_quant_strategy_tool.Runner") as runner:
        runner.return_value.execute.side_effect = timeout
        result = json.loads(BacktestLocalQuantStrategyTool().execute(**tool_args()))
    assert result["status"] == "error"
    run = Path(result["run_dir"])
    assert json.loads((run / "state.json").read_text())["status"] == "failed"
    assert (run / "logs/runner_stdout.txt").read_text() == "partial progress"


def test_success_exit_without_real_metrics_is_not_a_success(tool_environment):
    from src.runtime.tools.local_quant_strategy_tool import BacktestLocalQuantStrategyTool

    with patch("src.runtime.tools.local_quant_strategy_tool.Runner") as runner:
        runner.return_value.execute.return_value.success = True
        result = json.loads(BacktestLocalQuantStrategyTool().execute(**tool_args()))
    assert result["status"] == "error"
    assert json.loads((Path(result["run_dir"]) / "state.json").read_text())["status"] == "failed"


def test_unknown_agent_paths_are_rejected(tool_environment):
    from src.runtime.tools.local_quant_strategy_tool import BacktestLocalQuantStrategyTool

    result = json.loads(BacktestLocalQuantStrategyTool().execute(**{**tool_args(), "quant_root": "/tmp"}))
    assert result["status"] == "error"


def test_discovery_refuses_extra_parameters(tool_environment):
    from src.runtime.tools.local_quant_strategy_tool import ListLocalQuantStrategiesTool

    result = json.loads(ListLocalQuantStrategiesTool().execute(path="/tmp/secret"))
    assert result["status"] == "error"


def test_autonomous_research_peers_can_load_and_run_local_strategies(tool_environment):
    from src.runtime.swarm.presets import load_preset
    from src.runtime.tools import build_swarm_registry

    preset = load_preset("autonomous_research_team")
    for spec in preset["agents"]:
        if "research" not in spec.get("capabilities", []):
            continue
        registry = build_swarm_registry(spec["tools"], skill_allowlist=spec["skills"])
        assert registry.get("backtest_local_quant_strategy") is not None
        assert registry.get("list_local_quant_strategies") is not None
        assert registry.get("load_local_quant_strategy") is not None
        loaded = json.loads(registry.execute("load_skill", {"name": "local-quant-strategy"}))
        assert loaded.get("status") != "error", loaded


@pytest.mark.parametrize(
    "name, arguments",
    [("list_local_quant_strategies", {}), ("load_local_quant_strategy", {"strategy_id": "ma20"})],
)
def test_discovery_accepts_worker_injected_context(tool_environment, name, arguments):
    from src.runtime.swarm.worker import _tool_arguments
    from src.runtime.tools import build_swarm_registry

    _, runtime = tool_environment
    workspace = runtime / "runs/parent/artifacts/worker"
    workspace.mkdir(parents=True)
    registry = build_swarm_registry([name])
    bound, refusal = _tool_arguments(registry.get(name), arguments, workspace)
    assert refusal is None
    assert json.loads(registry.execute(name, bound))["status"] == "ok"


def test_worker_can_backtest_then_read_report_inside_its_workspace(tool_environment):
    from src.runtime.swarm.worker import _tool_arguments
    from src.runtime.tools import build_swarm_registry

    _, runtime = tool_environment
    workspace = runtime / "runs/parent/artifacts/worker"
    workspace.mkdir(parents=True)
    name = "backtest_local_quant_strategy"
    registry = build_swarm_registry([name, "read_file"])
    bound, refusal = _tool_arguments(registry.get(name), tool_args(), workspace)
    assert refusal is None
    result = json.loads(registry.execute(name, bound))
    assert result["status"] == "ok", result
    path = Path(result["artifacts"]["summary.md"])
    assert path.is_relative_to(workspace)
    read_args, refusal = _tool_arguments(registry.get("read_file"), {"path": str(path)}, workspace)
    assert refusal is None
    content = json.loads(registry.execute("read_file", read_args))
    assert content.get("status") != "error", content
    assert "总收益率" in content["content"]


def test_local_quant_metrics_ground_a_completed_backtest_report(tmp_path):
    from src.runtime.agent.grounding import GroundingLedger

    ledger = GroundingLedger(run_dir=tmp_path, user_message="回测本地量化策略并汇总收益")
    ledger.ingest_tool_result(
        tool_name="backtest_local_quant_strategy",
        arguments={},
        call_id="quant-run",
        success=True,
        result=json.dumps({"status": "ok", "metrics": {"total_return": 0.08, "sharpe": 0.8}}),
    )
    answer = "实际回测总收益率为8%，夏普为0.8。\n\n```figures\n8% | observed | total_return | quant-run\n0.8 | observed | sharpe | quant-run\n```"
    validated = ledger.validate_final_answer(answer)
    assert validated.valid, validated.issues
    assert ledger._analysis_completed, "the actual backtest must count as completed analysis"


def test_real_worker_loads_backtests_and_reads_report_with_scripted_model(tool_environment, monkeypatch):
    import src.runtime.swarm.worker as worker
    from src.runtime.providers.chat import LLMResponse, ToolCallRequest
    from src.runtime.swarm.models import SwarmAgentSpec, SwarmTask

    class ScriptedModel:
        step = 0

        def stream_chat(self, messages, **kwargs):
            self.step += 1
            sequence = [
                ("list_local_quant_strategies", {}),
                ("load_local_quant_strategy", {"strategy_id": "ma20"}),
                ("backtest_local_quant_strategy", tool_args()),
            ]
            if self.step <= 3:
                name, args = sequence[self.step - 1]
            elif self.step == 4:
                backtest = json.loads(messages[-1]["content"])
                assert backtest["status"] == "ok", backtest
                name, args = "read_file", {"path": backtest["artifacts"]["summary.md"]}
            else:
                return LLMResponse(
                    content="Completed the local strategy backtest and read its actual portfolio report.",
                    tool_calls=[],
                    finish_reason="stop",
                )
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id=f"step-{self.step}", name=name, arguments=args)],
                finish_reason="tool_calls",
            )

        def close(self):
            pass

    _, runtime = tool_environment
    monkeypatch.setattr(worker, "ChatLLM", lambda **kwargs: ScriptedModel())
    result = worker.run_worker(
        SwarmAgentSpec(
            id="researcher",
            role="Researcher",
            system_prompt="Load and backtest local strategies.",
            tools=[
                "list_local_quant_strategies",
                "load_local_quant_strategy",
                "backtest_local_quant_strategy",
                "read_file",
            ],
            max_iterations=6,
        ),
        SwarmTask(id="research", agent_id="researcher", prompt_template="Backtest ma20 and summarize the report."),
        {},
        {},
        runtime / "runs/worker_e2e",
    )
    assert result.status == "completed", result
    assert any(path.endswith("summary.md") and "local_quant" in path for path in result.artifact_paths)
