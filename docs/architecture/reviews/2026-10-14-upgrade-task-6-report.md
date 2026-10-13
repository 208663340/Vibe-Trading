> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 6 implementation report

Status: DONE. Branch `codex/architecture-stability-upgrade`.
Base: `37e1e75755dfaeb8b75e45d5b85292c0c2a59fa0`.
Signed local commit: `d2e0601af0545b0d992e0b81fac591443d18eeb0` — `fix(agent): own run lifecycle and account for actual request budgets`.
DCO trailer verified in `git log -1 --format=%B`: `Signed-off-by: WillHe-Polymaker <will.he@polymaker.com>`.
Only the 10 Task 6 production/test files are in this commit; controller docs remain unstaged.

## Delivered behavior

- Added a focused `RunLifecycle` owner for one generation, thread-safe stop requests, monotonic true progress, terminal claim and done/cleanup. Watchdog requests stop and emits diagnostic events only. The actual main owner claims and persists one outcome after its provider/tool work returns.
- Existing stall reason wins over later user cancellation before terminal claim; requests after the claim cannot change it. Facade reuse rejects a concurrent run without clearing its stop event. The initial queued cancellation is retained; sequential reuse installs a fresh owner and revokes previous generation helpers.
- Non-returning writes remain owned and in progress/stopping: no fabricated terminal, released claim or detached write/retry. Barrier coverage checks both a blocked provider and a blocked write; one matching result/state/end appears only after the owner resumes and exits.
- Run's outer finally covers lifecycle setup, context binding, run/request/context/trace initialization, loop errors, state/end writes and close failures. It attempts trace close, resets session context and marks done/releases the facade gate even on faults. An actual close operation that never returns still retains ownership.
- One main terminal claim feeds both normal and exception result paths. Optional run state/end/close artifact faults are logged and cannot rewrite or discard the claimed result delivered to SessionService. Startup/prefinalization exceptions with a known run directory become a failed claimed outcome; inability to create/identify a run directory still propagates after cleanup.
- Timer event admission is generation/lifecycle fenced. Admission linearizes under the lifecycle lock and external event callbacks execute outside the lock; a blocked heartbeat emitter cannot prevent cancellation or final ownership cleanup.
- Context accounting snapshots the request's message estimate, actual sent tools estimate, configured model and monotonic request ID before every initial/retry main stream. Tools→text-only→tools uses actual schema deltas. Configured-model changes discard calibration; response aliases remain runtime metadata and do not reset configured identity. Invalid/missing usage cannot replace valid calibration. Compaction summary usage never calibrates main requests.
- Immutable static context at/above the usable window/cost ceiling produces an actionable failed result before provider/summarization admission. Removed the impossible 8K conversation floor; the 8K constant still serves the existing post-compaction regrowth threshold.
- RunStateStore writes JSON to a same-directory temporary file, flushes/fsyncs it, and atomically replaces the destination. A serialization/fsync/replace failure leaves the prior complete JSON record intact; temporary files are cleaned on handled failures.

## Files and exact interfaces

Production files are only the approved Task 6 scope:

- `agent/src/agent/run_lifecycle.py` (new): `RunLifecycle(generation: int, cancel: threading.Event | None = None)` exposes `generation`, `cancel`, `done`, `stall_reason`; `should_stop() -> bool`, `request_stop(reason: str | None = None) -> bool`, `progress()`, `idle_seconds() -> float`, `emit(callback: Callable[[], None])`, `claim_terminal(candidate: RunOutcome) -> RunOutcome`, `finish()`.
- `agent/src/agent/loop.py`: public `AgentLoop.run/cancel` signatures unchanged; `_cancel_event` and `_run_done` remain Event seams, `_stall_reason` remains readable through a lifecycle-backed property. Existing source/tests contain no `_stall_reason` assignment consumers. `_stop_predicate()` captures the lifecycle, with the original facade Event fallback for preserved standalone/object.__new__ tool helper callers. `_tool_invoker()` captures lifecycle stop/emission and retains injected facade `HeartbeatTimer`. `_process_tool_calls()` stop checks capture one owner; `_observe_tool_completion()` alone refreshes true tool progress; ordered `_finalize_tool_result()` retains raw grounding ingest before truncation/redaction and no longer credits synthetic commits as progress. `_persist_terminal()` is the unique main-owned artifact boundary. `_stall_watchdog(trace, run_dir, state_store, stall_timeout, lifecycle=None)` retains its first four positional arguments and accepts the captured owner used by real run threads. Main provider scopes and compaction scopes capture the owner's predicate. Helper/re-export surfaces remain available.
- `agent/src/agent/context_budget.py`: immutable `RequestSnapshot(request_id, model, messages, tools, conversation)`; `ContextMeter.snapshot(messages, tool_schema_tokens=None, *, model=None) -> RequestSnapshot`; compatibility `observe(input_tokens, messages=None, *, request=None)`, `tokens(messages, tool_schema_tokens, *, model=None)`, `static(messages, tool_schema_tokens, *, model=None)`. Legacy positional usage remains supported. Same-model prediction is anchor actual + message delta + actual tools delta.
- `agent/src/agent/run_outcome.py`: existing exports and result fields retained; stall `RunOutcome.mark` is now `failure` because the main owner performs persistence.
- `agent/src/core/state.py`: existing `RunStateStore` methods/signatures retained; atomic replacement is confined to `_write_json()`.

Tests changed/created: `test_run_lifecycle_races.py` (new), `test_agent_loop_stall_watchdog.py`, `test_context_budget.py`, `test_run_outcome_module.py`, `test_state_fsync.py`. No prompt/schema/model choice/grounding policy, framework/dependency/database/API DTO change.

## TDD evidence

Initial RED before production edits:

```sh
.venv/bin/python -m pytest agent/tests/test_run_lifecycle_races.py agent/tests/test_state_fsync.py agent/tests/test_context_budget.py -q --tb=short
```

Result: **15 failed, 26 passed in 7.61s**. Failures were seven stranded cleanup boundaries (request/context/trace constructor/start/end/state/close), concurrent reuse not rejected, watchdog writing terminal before owner exit, late timer emitting after completion, fsync corrupting prior state, replace not used, missing request snapshot/tool-model shape support (two tests), and immutable static overflow reaching the provider instead of actionable failure. The absent snapshot methods were expected missing-interface RED, not collection errors.

Additional self-review RED→GREEN:

- `... test_run_lifecycle_races.py::test_old_watchdog_paused_after_stop_cannot_poison_reused_owner -q --tb=short`: **1 failed** (stale old watchdog reason assigned into the reused owner), then lifecycle-backed facade reason gave GREEN; later related command gave **60 passed in 1.75s**.
- `... test_run_lifecycle_races.py::test_blocked_timer_emitter_cannot_block_stop_or_owner_finish -q --tb=short`: **1 failed in 1.11s**, `stop admission must not wait on user callback`; after outside-lock callback invocation the lifecycle race file gave **15 passed in 0.87s**. Root explicitly approved this emission-admission contract.
- `... test_run_lifecycle_races.py::test_initial_helper_predicate_is_revoked_when_first_generation_starts -q --tb=short`: **1 failed in 0.57s**, obsolete generation predicate returned False. Revoking the prior initial owner before publishing the first owner fixed it; final test verifies actual obsolete readonly invocation yields `tool_cancelled`, executed=False and zero effects.

The first broad covering run found **33 failed, 1,407 passed in 20.45s**, exclusively preserved standalone/object.__new__ helper fixtures lacking `_lifecycle`; no grounding/replay assertions were weakened. The narrow Event fallback restored these callers. Intermediate broad GREEN was **1,441 passed**; after emitter/schema/coverage changes it was **1,444 passed**; final verification below includes all final edits.

### Exact legacy assertions changed

- Old `agent/tests/test_run_outcome_module.py::TestResolveFinalStatus::test_stall_wins_and_does_not_remark` expected `mark is None`, with the rationale that the watchdog had already persisted failure. Renamed to `test_stall_wins_and_main_owner_marks_failure`, now `mark == "failure"`.
- `agent/tests/test_run_outcome_module.py::TestResolveFinalStatus::test_ladder_precedence_stall_beats_cancel` also expected `mark is None`; now expects `"failure"`, retaining failed status/precedence.
- `test_agent_loop_stall_watchdog.py::test_watchdog_fails_a_run_with_no_forward_progress` now requires stop request plus **no terminal state/end written by the watchdog**. Main-owner barrier tests separately require exactly one matching final result/state/end after actual owner exit.
- Old `test_context_budget.py::test_a_window_smaller_than_the_static_prompt_still_has_a_floor` asserted the impossible 8K floor. Renamed `test_a_window_smaller_than_the_static_prompt_has_no_impossible_conversation_floor`; conversation is zero, and a separate end-to-end test proves actionable failure without any provider admission.

These implement the explicitly approved new ownership/budget contracts, not a relaxed guarantee of cleanup or evidence correctness.

## Final focused verification

```sh
.venv/bin/python -m pytest agent/tests/test_agent_loop_*.py agent/tests/test_agent_grounding.py agent/tests/test_agent_goal_context.py agent/tests/test_grounding_*.py agent/tests/test_auto_compact_chunked_summary.py agent/tests/test_compaction_call_identity.py agent/tests/test_context_budget.py agent/tests/test_context_attribution_layers.py agent/tests/test_readonly_replay_after_microcompact.py agent/tests/test_technical_indicator_compaction_replay.py agent/tests/test_tool_*.py agent/tests/test_loop_helpers.py agent/tests/test_run_outcome_module.py agent/tests/test_state_fsync.py agent/tests/test_run_lifecycle_races.py agent/tests/test_provider_call_runner.py agent/tests/test_chat_llm_*.py agent/tests/test_terminal_close_accounting.py -q --tb=short
```

**1,448 passed, 9 warnings in 16.88s**, exit 0. Local full output `/private/tmp/task6-covering-final-verified.log`. Warnings are existing NumPy generic-timedelta deprecations in `agent/backtest/loaders/ccxt_loader.py:39-47`. No warning filters added. Includes all loop, outcome/state, context/compaction, raw grounding/release, replay/write barriers, readonly/provider lifecycle, thought signatures, DSML, no-assistant-prefill and provider integration covering tests.

```sh
.venv/bin/ruff check agent/src/agent/run_lifecycle.py agent/src/agent/loop.py agent/src/agent/context_budget.py agent/src/agent/run_outcome.py agent/src/core/state.py agent/tests/test_run_lifecycle_races.py agent/tests/test_agent_loop_stall_watchdog.py agent/tests/test_context_budget.py agent/tests/test_state_fsync.py agent/tests/test_run_outcome_module.py
git diff --check
```

**All checks passed**, diff check exit 0. Final race file separately **19 passed in 0.93s**. Tests use local temporary files/fake operations/Event barriers, release blocked workers in finally, and call no external provider or broker.

## Self-review, precise limits and Task 7 handoff

- Reviewed all production/test diff, including initially overlooked stale watchdog reason mutation and callback-lock latency; both received failing behavioral regressions and fixes. Facade Event/private helper/re-export compatibility retained. Grounding raw ingest/replay identity unchanged. No uncertain write retry, fake thread kill or early main ownership release introduced.
- When physical storage fails, run `state.json`/terminal trace may be absent or retain its previous valid record; their success cannot be promised. The unique claimed main outcome still returns to SessionService for its separate durable terminal persistence, with the real run artifact fault logged. An actual underlying close failure is logged and cleanup is attempted; resource closure itself cannot be guaranteed when close fails.
- Admission prevents new timer emissions after terminal/done/obsolete generation. An external callback already admitted may finish later and cannot be forcibly withdrawn. It never holds the stop/terminal ownership lock. Non-returning provider/write/close owners retain ownership until actual return; detached revoked readonly/provider workers remain governed by prior process capacity fences/counters.
- RunStateStore guarantees complete-file atomic replacement on handled failures/process interruption, not a new multi-file transaction or proven power-loss durability; parent-directory fsync is not added here. State/trace artifact persistence failures are explicitly visible in logs.
- Final whole-backend/frontend regression, independent Task 6 review and whole-branch review belong to root Task 7. Run the session/execution/runtime integration suites there as well. Pay attention to unique main result authority under artifact faults and emitter in-flight limitation, and the new `mark="failure"` owner contract.
- Controller design/plan docs remain unstaged; only Task 6 production/tests are staged/committed. This report stays in the provided locally ignored artifact directory for root collection. No remote/live/network/account/credential/history-recovery operations or subagents were used.

## Review fix round 1/5 — Important I1 (2026-10-14)

Fix base: `879cf97d` (`docs(architecture): clarify terminal artifacts and callback admission`). Independent review `task-6-review.md` correctly identified loss of cancellation when the old lifecycle was marked done before the new owner was published. Self-review also identified the related earlier reuse window immediately after run admission: cancellation could still select the finished old owner before publication began.

### Scoped fix and interfaces

Only `agent/src/agent/loop.py` and `agent/tests/test_run_lifecycle_races.py` changed in this fix round. Added private `_handoff_gate: threading.Lock` in the facade. `run()` acquires its nonblocking `_run_gate` claim and publishes lifecycle plus `_cancel_event`/`_run_done` aliases under the dedicated handoff gate. `cancel()` selects and stops the published lifecycle under the same gate. The prior initial helper owner still receives `finish()` and remains revoked; initial pre-start cancellation still shares the original Event into the first real owner. Deliberate sequential reuse gets a fresh Event, while concurrent run rejection neither alters nor cleans up another owner.

The handoff gate encloses only admission, in-memory reset/constructor, previous-owner retirement and owner/alias publication. Session binding, context/request/trace I/O, provider/tool work, terminal artifact persistence, logging and external callbacks remain outside this gate. Cancellation during an active run never waits on the full run gate. Outer cleanup is conditional on successful admission, so an inadmissible concurrent caller cannot close another owner's trace, set its done Event or release its run claim.

### Barrier RED→GREEN

New test: `test_run_lifecycle_races.py::test_cancel_during_owner_handoff_reaches_published_generation`, parametrized as `[admission-first-run]`, `[admission-reuse]`, `[retirement-first-run]`, `[retirement-reuse]`.

The test pauses actual run-gate admission or the previous owner's real `finish()`. Cancellation signals from its actual old-owner stop read or its blocked dedicated-gate acquisition, eliminating a race with an unscheduled cancellation thread. Publication is then released, and the acknowledged stop precedes the first possible provider/tool checkpoint. All blocked threads are released in `finally`. Assertions require zero provider effects, zero tool effects, a cancelled result with the same reason as exactly one new end/state record, consistent lifecycle/Event aliases and done set. Reuse retains its prior successful end separately and verifies exactly one new cancelled end.

```sh
.venv/bin/python -m pytest agent/tests/test_run_lifecycle_races.py::test_cancel_during_owner_handoff_reaches_published_generation -q --tb=short
```

RED on the original fix-base loop: **3 failed, 1 passed in 0.61s**. `[admission-reuse]`, `[retirement-first-run]`, `[retirement-reuse]` each executed two provider calls instead of zero. Existing `[admission-first-run]` correctly retained its original shared cancellation Event and passed. The original authorized HEAD loop was temporarily restored only for this focused RED using `git show HEAD:agent/src/agent/loop.py`; fixed working bytes were restored in `finally`. No confidential/history recovery or other files were touched.

GREEN after the fix: **4 passed in 0.81s**.

### Narrow covering and static verification

```sh
.venv/bin/python -m pytest agent/tests/test_run_lifecycle_races.py agent/tests/test_agent_loop_terminal_state.py agent/tests/test_agent_loop_stall_watchdog.py agent/tests/test_tool_execution_lifecycle.py agent/tests/test_provider_call_runner.py agent/tests/test_terminal_close_accounting.py -q --tb=short
```

**83 passed, 9 warnings in 5.40s**, exit 0. Full local output `/private/tmp/task6-fixround1-covering.log`. Includes initial-helper revocation, initial pre-start cancellation, concurrent rejection, responsive cancellation with blocked provider/write/timer callbacks, old-watchdog fencing, main terminal authority and watchdog cleanup. The nine warnings are the already tracked `ccxt_loader.py:39-47` NumPy deprecations; no suppression added.

```sh
.venv/bin/ruff check agent/src/agent/loop.py agent/tests/test_run_lifecycle_races.py
.venv/bin/python -m py_compile agent/src/agent/loop.py agent/tests/test_run_lifecycle_races.py
git diff --check
```

Ruff: **All checks passed**; syntax compilation and diff check exit 0. Reviewed the scoped diff for lock ordering, exception cleanup and absence of I/O/external callbacks under the new gate. Only gate→lifecycle-state short-lock acquisition is used; provider/tool/persistence semantics and grounding are unchanged. No remaining fix-round concerns; the original artifact-failure/in-flight callback limits still apply. Root's README module map remains unstaged, and full regression/fresh independent scoped review remain root-owned.

Fix signed local commit: `cd0682e557ecbdfaffa4a45257c77fefd672ae46` — `fix(agent): serialize cancellation with run owner handoff`. DCO trailer verified with `git log -1 --format=%B`: `Signed-off-by: WillHe-Polymaker <will.he@polymaker.com>`. Post-commit status contains only root's unstaged `README.md`; post-commit diff check is clean.
