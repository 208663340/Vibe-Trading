> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 6 independent review

## Spec Compliance

- ❌ Issues found: cancellation is lost during the initial lifecycle handoff at `agent/src/agent/loop.py:380`. This violates preservation of initial cancellation and reliable run stop ownership. See Important I1.
- ✅ The remaining inspected Task 6 changes implement the requested main-owner terminal boundary, cleanup, true progress, request-shape accounting and atomic state replacement; evidence is listed below. All five required production files and the relevant tests have hunks in `review-37e1e757..879cf97d.diff` (base `37e1e75755dfaeb8b75e45d5b85292c0c2a59fa0`, head `879cf97d`; production commit `d2e0601a`). Controller-owned contract docs are consistent with the stated artifact-failure and callback-admission rulings.
- ⚠️ Cannot verify from this task diff: end-to-end SessionService persistence/ownership and API shutdown integration, all Task 1–5 guarantees, full backend/frontend regression, build and whole-branch review. Those remain Task 7 controller gates. This review does not infer their failure or rerun their suites.

## Strengths

- `agent/src/agent/run_lifecycle.py:59`, `agent/src/agent/loop.py:1449`: the main flow claims one outcome; late stops cannot rewrite it, and artifact write errors log without replacing the claimed result. `agent/tests/test_run_lifecycle_races.py:91`, `:192`, `:327` exercise matching terminal outcomes, late stops, and real-watchdog cleanup under artifact faults.
- `agent/src/agent/loop.py:400`, `agent/tests/test_run_lifecycle_races.py:34`, `:150`: outer cleanup covers startup and terminal faults; blocked mutating work retains the facade gate until actual return. No forced termination or uncertain-write retry is introduced.
- `agent/src/agent/run_lifecycle.py:50`, `agent/src/agent/loop.py:1720`, `agent/tests/test_run_lifecycle_races.py:129`, `:273`: generation/lifecycle admission fences obsolete timer work, and external callbacks execute outside the lifecycle lock. The blocked-emitter regression matches the controller's explicit admission contract.
- `agent/src/agent/loop.py:1731`, `agent/tests/test_run_lifecycle_races.py:214`: progress updates are monotonic and limited to accepted executed completions; synthetic replay/cache/compact/capacity and revoked outcomes do not refresh the watchdog. The focused dependency check at `agent/src/agent/tool_execution.py:378` and `:410` confirms these observation callbacks are caller-side.
- `agent/src/agent/context_budget.py:184`, `:190`, `:208`: immutable request estimates and monotonic IDs preserve actual sent message/tools/model identity; the prediction includes tool deltas and invalid usage does not damage calibration. `agent/tests/test_context_budget.py:375`, `:392`, `:418`, `:450` cover hand-derived tools/text-only/tools estimates, mutation after dispatch, configured model versus response aliases, and summary usage isolation.
- `agent/src/agent/loop.py:1838`, `agent/tests/test_context_budget.py:401`: impossible immutable context fails actionably before provider admission. `agent/src/core/state.py:81` and `agent/tests/test_state_fsync.py:44`, `:56` preserve complete prior JSON until same-directory atomic replacement succeeds.

## Issues

### Critical (Must Fix)

- None found in the inspected Task 6 boundary.

### Important (Should Fix)

- **I1 — cancellation disappears between retiring and publishing the owner.** `agent/src/agent/loop.py:380` calls `previous.finish()` before `self._lifecycle = lifecycle` at `:381`. A concurrent `cancel()` at `:289` still reads the old lifecycle. `agent/src/agent/run_lifecycle.py:32` rejects the stop because that lifecycle is now done. Even on the first run, where the new owner shares the initial cancel Event, no Event is set, so the new run invokes the provider and returns success. A bounded deterministic probe reproduced this: pause the initial owner's `finish()` immediately after its real implementation, call `agent.cancel()`, then release publication. Output was `cancel_event_after_cancel=False`, `result_status=success`, `provider_calls=['provider']`, `done=True` (exit 0). No existing reported test addresses cancellation inside this handoff; the initial-helper test at `agent/tests/test_run_lifecycle_races.py:310` checks obsolete tool dispatch instead. Serialize lifecycle selection/publication and `cancel()` using a short dedicated handoff lock, or an equivalent atomic cancellation handoff; retain old-helper revocation and never make cancellation wait on the full run gate. Add a barrier regression proving cancellation in this window reaches the new owner and prevents provider/tool execution, including deliberate facade reuse.

### Minor (Nice to Have)

- `agent/backtest/loaders/ccxt_loader.py:39`: the recorded covering test output contains nine NumPy generic-timedelta deprecation warnings through line 47. These are outside this diff and are not a Task 6 blocker; track dependency-compatible timedelta construction separately. No warning suppression was introduced.

## Checks and evidence

- Read the task brief/report/handoff, full current binding design and the supplied diff once in sequential portions. Recovered one output-truncated portion of the old-watchdog test from that same package.
- Inspected only omitted middle portions of changed functions needed to judge startup, pre-request compaction/nudges, tool-batch observation and budget control (`agent/src/agent/loop.py:430`, `:565`, `:1538`, `:1860`), plus the omitted pure result-builder tail (`agent/src/agent/run_outcome.py:169`). No Git metadata commands or broad crawl were run.
- Named dependency risks checked: partially initialized TraceWriter leaking an opened handle (`agent/src/agent/trace.py:73` constructor closes on its post-open initialization fault); new progress observer running on worker threads (`agent/src/agent/tool_execution.py:345` caller-owned batch loop); emitter failure behavior (`agent/src/agent/loop.py:2345` unchanged exception containment). These checks did not reveal additional blockers.
- Verified `/private/tmp/task6-covering-final-verified.log` ends with **1448 passed, 9 warnings in 16.88s** and names the above existing warning source. The report records initial **15 failed, 26 passed** RED, intermediate corrections, signed production commit and successful focused lint/diff checks. Those reported commands were not rerun.
- Ran exactly one new focused probe with `.venv/bin/python -` using the existing fake `build`/`response` helpers, a temporary directory, MonkeyPatch and two Events. All waits were bounded, the publication Event was released in `finally`, and the executor joined the only worker. No real provider, network, server, account or credential operation was used; production/tests/index/HEAD were not modified.

## Assessment

**Task quality: Needs fixes.**

The terminal/artifact separation and request budgeting changes are well scoped and supported by behavioral tests. The reproducible stop-loss window during lifecycle publication is a correctness gap in the task's central ownership contract and should be fixed before the Task 6 gate is approved.
