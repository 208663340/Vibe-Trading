# Local Quant Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let the agent load trusted quant strategies, screen stocks using close-time information, run a shared portfolio backtest, and summarize actual results.

**Architecture:** Three BaseTools use a trusted source catalog and declarative request models. A Runner subprocess adapts the original feature functions, applies causal screens and executes ChinaAEngine. Existing report artifacts and state contracts are reused.

**Tech Stack:** Python, Pydantic, pandas/numpy, stdlib AST, existing Runner and ChinaAEngine. No new dependencies.

**Spec:** docs/architecture/2026-10-19-local-quant-tools.md

## Global Constraints

- Keep external quant files unchanged.
- Model arguments cannot select Python files or data paths.
- Future label columns never enter the strategy frame.
- Each run is one shared portfolio.
- Preserve all unrelated working-tree changes, including custom-program work.
- Implement here on the existing codex feature branch; the previous managed worktree was outside this task's writable roots.

## Review Focus

- A source file changes between discovery and execution: save and execute the captured version.
- A selected stock lacks a later bar: do not select survivors using future availability.
- Empty screens or no completed trades: report cash/holdings honestly without fake activity.
- The caller requests a short window: indicators see warm-up, metrics do not.
- A failed or partial subprocess leaves CSVs: never report successful results from them.

### Task 1: Trusted catalog and request validation

**Files:** `agent/src/research/local_quant/{__init__,models,catalog}.py`, `agent/src/platform/config/env_schema.py`, `agent/tests/test_local_quant_catalog.py`.

**Interfaces:** `QuantRequest`, `CloseScreen`; `quant_root() -> Path`, `list_strategies(root) -> list[dict]`, `load_strategy(root, strategy_id) -> dict` (includes extracted feature code and source snapshots).

- [x] Write tests for readonly discovery, source-dependent feature code, future-operation refusal, unknown IDs, invalid numbers/dates and symlink escapes.
- [x] Run tests and observe failures due to missing behavior.
- [x] Implement bounded models, catalog and source extraction; add the operator root setting.
- [x] Run catalog tests; expected all pass.

### Task 2: Causal selection and shared portfolio execution

**Files:** `agent/src/research/local_quant/{pipeline,runner}.py`, `agent/tests/test_local_quant_pipeline.py`.

**Interfaces:** `build_targets(bars, features, request, strategy) -> (targets, decisions)`; `execute_job(run_dir) -> dict` reads captured job and code, writes native engine outputs and summary.

- [x] Write real-data-fixture tests for prior-only inputs, next-open fills, shared cash, fees, warm-up, no candidates, monthly carry and future-bar invariance.
- [x] Run the tests; expected failures for missing pipeline.
- [x] Implement causal CSV loading, screens/targets, engine adapters and persisted report.
- [x] Run pipeline + catalog tests; expected all pass.

### Task 3: Agent tools, workflow and integration

**Files:** `agent/src/runtime/tools/local_quant_strategy_tool.py`, `agent/src/research/skills/local-quant-strategy/SKILL.md`, `agent/.env.example`, `agent/tests/test_local_quant_tools.py`, `docs/local-quant-tools.md`.

**Interfaces:** Three autoloaded BaseTool classes; backtest takes the QuantRequest fields and returns bounded metrics/artifact envelope.

- [x] Write integration tests for discovery, real subprocess outputs and malformed/timeout/failure results.
- [x] Observe RED, implement tools with Runner and state lifecycle; document agent workflow and supported strategies.
- [x] Run new tests, relevant registry/engine/config tests, then full backend suite.
- [x] Execute a real local-data run; inspect actual fills, candidates, coverage and summary.
- [x] Obtain one fresh-context review, fix material findings with RED/GREEN tests, save verification record and commit only task files.

## Completion evidence

New feature tests: 53 passed. Full backend: 18,174 passed, 84 skipped.
One independent review completed; all four important execution findings reproduced RED and fixed GREEN.
Actual source-data run and decisions: `docs/verification/2026-10-19-local-quant-tools.md`.
Internal Agent/Swarm workspace injection, confined report publishing and grounding integration are covered.
