# Quant Research Workbench

A local quantitative research workbench for independent researchers and small teams. Use an external code agent to write and revise strategies; use this workbench to execute experiments, retain evidence, inspect reports and compare revisions.

[中文说明](README_zh.md) · [Product direction](docs/decisions/0003-external-agent-research-workbench.md)

The default application has four areas: **Experiments, Reports, Compare, Settings**. It starts no internal chat assistant, model provider, scheduled agent or Swarm. LLM credentials are not required.

## Start locally

Requires Python 3.11+ and Node 22.22+ for building the UI. From this repository:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e .
npm --prefix frontend ci
npm --prefix frontend run build
quant-workbench serve --workspace "$PWD"
```

Open http://127.0.0.1:8899. The default storage is `~/.vibe-trading/runs`; pass `--root /absolute/state-directory` for separate storage. One service owns a storage root. `--workers 2` controls concurrency (1–8); the pending/running queue is bounded at 64.

During frontend development, run `npm --prefix frontend run dev` alongside the service and open http://localhost:5899. The Vite proxy defaults to port 8899; `VITE_API_URL` changes its target.

An existing checkout can run the modules without reinstalling console scripts:

```sh
PYTHONPATH=agent .venv/bin/python -m src.interfaces.workbench_cli serve --workspace "$PWD"
```

## First experiment — offline, no model or data credentials

In another terminal, with the virtual environment activated:

```sh
quant-workbench submit examples/workbench/experiment.json
quant-workbench experiments --project offline-demo
quant-workbench status <run_id>
quant-workbench logs <run_id>
quant-workbench result <run_id>
quant-workbench cancel <run_id>
```

These commands emit JSON. Submission returns a stable `run_id`; execution continues independently of the CLI or MCP client. Poll until `success`, `failed`, `cancelled` or `interrupted`. Cancellation first returns `cancelling` while execution stops. Timeouts are failures. A restarted service marks previously unfinished records `interrupted`; it does not claim an orphaned process has stopped.

The sample uses synthetic daily prices, fixed position size and no fees/slippage. It demonstrates report integration, not strategy quality. Duplicate its manifest, change `args` to `["--shares", "3"]`, set `parent_run_id` to the first run and describe the change in `change_note`. Submit again and select both completed experiments in the workbench to compare them.

## Program and evidence contract

A manifest names `source_dir`, explicit `files`, `config`, `name`, `project`, optional `parent_run_id`, `change_note` and `timeout_seconds` (1–86400). In the CLI, `source_dir` is relative to the manifest file. In HTTP/MCP, relative paths are relative to the service's selected workspace.

`config.mode="program"` requires a Python `entrypoint` included in `files`; `args` is an optional list of strings. Source and input files are copied before queuing and recorded with SHA-256, up to 512 files / 100 MiB per submission. Include imported project modules and local data. Dot paths, symlinks and paths outside the selected workspace are rejected. Changing the original file after submission does not change the submitted copy.

The program runs with the snapshot as its working directory, reads the saved configuration from `RUN_DIR/config.json`, and writes:

| File under `RUN_DIR/artifacts` | Required columns |
| --- | --- |
| `equity.csv` | `timestamp,equity,ret,drawdown` |
| `trades.csv` | `timestamp,code,side,pnl,reason,return_pct` |
| `metrics.csv` | `final_value,total_return,annual_return,sharpe,max_drawdown,win_rate,trade_count` |

Equity requires rows; zero trades are valid with a header-only trades file. Metrics must be finite numbers and trade count a nonnegative integer. Each successful run gets the existing report format plus source/config metadata and explicit check results. `artifact_schema=passed` does **not** mean accounting reconciliation or out-of-sample validation passed: both are currently `not_run`. Program-supplied metrics are not independently recomputed in M0.

The existing built-in engine is also available using `config.mode="builtin"`, its normal configuration, and `code/signal_engine.py` in `files`. Existing data-loader configuration still applies. Provider data and files not explicitly listed are outside the source/input snapshot; packages, network responses and the full execution environment are not frozen. Do not infer complete reproducibility from a source hash alone.

Local programs are trusted Python code executing with the operator's OS permissions. Workspace checks restrict admitted inputs; they are not an OS sandbox. Keep the service on loopback. Existing API key and origin checks apply; configure `API_AUTH_KEY` if required and save the same key in Workbench Settings or the external client's environment.

## External code agents and MCP

Start the workbench service first. Configure any compatible MCP client to launch:

```json
{
  "mcpServers": {
    "quant-research": {
      "command": "/absolute/path/to/.venv/bin/quant-workbench-mcp",
      "args": ["--url", "http://127.0.0.1:8899"]
    }
  }
}
```

The tools are `workbench_info`, `submit_experiment`, `list_experiments`, `get_experiment`, `get_experiment_result`, `get_experiment_logs`, `cancel_experiment`. They call the same HTTP service used by the UI. MCP clients do not start another executor or own background jobs. The CLI offers the same bridge with `quant-workbench mcp`.

Agent workflow: inspect workspace → write strategy and manifest → submit → poll state/logs → inspect report and unchecked evidence → revise with a parent run ID. Use the workbench's **Copy context** action to hand an experiment's configuration, hashes and results back to the external agent.

## Migration scope

The workbench uses a neutral research identity. Its old chat, Swarm, model/channel settings, scheduled-assistant and live-control frontend implementations have been removed, together with their client APIs, translations and dedicated dependencies. Shared report metrics were moved into the report component directory.

`quant-workbench` and `quant-workbench-mcp` are the preferred commands. The `vibe-trading` and `vibe-trading-mcp` aliases, existing `~/.vibe-trading` storage and stored API-key identifier are retained for compatibility; no user experiments or credentials are migrated or deleted. Backend assistant/session/Swarm implementations and Python dependencies remain outside the default startup path for legacy reverse dependencies. Old deployment launchers still require a separate migration; use the commands on this page.

Next milestones are deep loss diagnosis and comparison (M1), genuine train/test robustness validation (M2), then batch search and candidate screening (M3). The current UI does not claim these later checks are implemented.

## Verification

```sh
PYTHONPATH=agent .venv/bin/python -m pytest agent/tests/test_workbench_experiments.py agent/tests/test_workbench_api.py agent/tests/test_workbench_clients.py -q
npm --prefix frontend run test:run
npm --prefix frontend run build
```

Based on [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading). Licensed under [MIT](LICENSE). Existing backtest engines, data integrations and report visualizations retain their original attribution and licenses.
