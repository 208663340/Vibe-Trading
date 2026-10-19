# Autonomous Swarm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Ship an opt-in peer-claiming research swarm with durable leases, bounded execution, restart recovery and compatible read interfaces.

**Architecture:** Existing workers pull eligible ready tasks from a per-run SQLite blackboard. Durable epochs fence result publication; the existing DAG path remains the default.

**Tech Stack:** Python 3.11+, sqlite3, Pydantic 2, existing Worker/FastAPI/SSE, pytest.

**Spec:** `docs/architecture/2026-10-19-autonomous-swarm.md`

## Global Constraints

- `dag` remains the default; preserve existing tools, credentials and trading checks.
- Single-host SQLite only; no new service or package dependencies.
- Work in the authorized checkout after native worktree writes were denied; preserve all pre-existing modifications.
- Implement inline, then perform one independent whole-change review. No push, merge or deployment.

## Review Focus

- A crashed owner returns after a newer attempt has committed: its result and artifacts cannot replace the winner.
- Multiple runtime instances recover one run: database admission and a local semaphore bound their workers.
- Process dies after committing a task but before run.json/event projection: reads still expose durable truth.
- Failed prerequisites and cancelled/deadline-expired runs never admit downstream work.
- Retry/resume and SSE cursors preserve accepted outputs and avoid replaying duplicate events.

### Task 1: Transactional blackboard

**Files:** Create `agent/src/runtime/swarm/blackboard.py`, `agent/src/runtime/swarm/autonomous_models.py`, `agent/tests/test_swarm_blackboard.py`; extend `models.py`.

**Interfaces:** `Blackboard.initialize(run, max_workers)`, `claim(agent_id) -> TaskLease | None`, `renew(lease) -> bool`, `complete(lease, result) -> bool`, `cancel()`, `snapshot(run) -> SwarmRun`, `read_events(after_index=0)`.

- [x] Write tests for independent-connection claim races, expiry fencing, dependency failure, duplicate commits, cancellation and durable reload.
- [x] Run `.venv/bin/python -m pytest agent/tests/test_swarm_blackboard.py -q`; observe missing-feature failure.
- [x] Implement validated autonomous settings, schema and transactional state transitions.
- [x] Run the same command; require all passing.

### Task 2: Peer execution and recovery

**Files:** Create `agent/src/runtime/swarm/autonomous.py`, `agent/tests/test_swarm_autonomous.py`; extend `runtime.py` and `store.py`.

**Interfaces:** Consume Task 1's lease API. Add `SwarmRuntime.recover_run(run_id, ...) -> SwarmRun`; route autonomous execution/reads to the blackboard.

- [x] Write integration tests for capability selection, ready-task progress without layer barriers, failed dependencies, cancellation, restart recovery, retry/resume and isolated attempt outputs.
- [x] Run focused tests and observe missing-mode/recovery failures.
- [x] Implement bounded peer loops, lease renewal, immutable deadline, event delivery and storage projection.
- [x] Run blackboard/autonomous and existing Swarm regression tests; require all passing.

### Task 3: Usable preset, API and documentation

**Files:** Extend `presets.py`, `serialization.py`, `interfaces/api/swarm_routes.py`; add a research-only autonomous preset, route/preset tests and `docs/decisions/0002-autonomous-swarm-blackboard.md`.

**Interfaces:** Existing create-run/CLI/tool routes select mode from the trusted preset. Add authenticated `POST /swarm/runs/{run_id}/recover`. Public metadata includes mode, capabilities, attempts and lease expiry.

- [x] Write tests for valid capability presets, invalid graphs/owners/settings, recovery route conflicts/auth and event cursor continuity.
- [x] Observe failures, implement the preset/API contract and document use/deployment limits.
- [x] Run focused Swarm tests, full backend pytest, frontend regression tests where the shared read contract is affected, lint and diff checks.
- [x] Obtain independent review, fix material findings with regression tests, and record verification results and limits.
