> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 5 — Tool execution, admission and write-safe replay

Status: DONE_WITH_CONCERNS (focused verification complete; independent review and
Task 7 integration remain). Base: a422e87dbb54fc6c8a191b47003c1f3886493457.
Signed local commit: **784c39d70bda4e910ecec9034c74d2b1fb92766b** `fix(agent): bound tool execution and fence replay across writes`.

## Implemented boundary and precise interfaces

New `agent/src/runtime/agent/tool_execution.py` separates invocation/resource ownership
from finite admission, early completion observation and ordered caller commits.
New `agent/src/runtime/agent/tool_replay.py` decides observation validity at dispatch.
No production dependency, configuration or schema was added.

- `PreparedToolCall(index, call_id, name, arguments, readonly, call=None,
  decision=None)` is frozen. `arguments` are normalized for execution, while the
  original provider call remains available for the existing parameter identity.
- `ToolExecutionResult(prepared, raw_result, elapsed_ms, executed, outcome,
  exception=None)` is frozen, with `index` and `call_id` properties. `executed`
  distinguishes actual implementation dispatch from denied, unknown, capacity,
  cancelled-before-start and synthetic paths. A cancelled/timed-out *started*
  reader retains executed=True without accepting its late external result.
- `ReadonlyToolBudget(capacity=32)` is privately injectable; `active_count` and
  `abandoned_count` are read-only snapshots. Omitted budgets use one process
  default, shared across AgentLoops and successive RuntimeServices lifespans.
  Leases cover underlying registry execution and thread-local cleanup; only its
  actual worker finally releases. Caller cancellation/deadline never releases.
  Thread-start/deepcopy failures release without falsely leaving a live worker.
- `abandoned_readonly_tool_count()` observes revoked, still-live readers in the
  process-default budget only. Private injected budgets are excluded.
- `ToolInvoker(execute, *, should_cancel, emit, timeout_s=None,
  heartbeat_interval_s=3, budget=None, before_write=None,
  heartbeat_factory=HeartbeatTimer)`: `start(prepared)` returns a private reader
  handle or an explicit synthetic result; `poll(reader)` returns a settled result
  or None; `invoke(prepared)` waits with cancellation still enabled for timeout
  zero/None. Positive readonly timeout is monotonic. Diagnostic error codes:
  `tool_timeout`, `tool_cancelled`, `tool_capacity`, `tool_exception`.
- Readonly workers receive independent deep-copied execution args and copied
  ContextVars. They write only their private raw result/exception and bounded
  32-entry progress deque. Progress drops oldest entries when backlogged; caller
  drains and forwards it, including final progress if a callback stalled during
  completion. Read heartbeats are caller-polled. Revocation fences all late raw
  results and progress; no worker mutates transcript, evidence, cache or trace.
- Writes execute on the owning caller. Stop is rechecked immediately before the
  mutation callback and registry.execute. `before_write()` invalidates mutable
  replay immediately before actual execute, even if execute returns error,
  raises after effects, or later result persistence fails. An active write is
  awaited to real completion; existing heartbeat and one timeout-warning timers
  remain. `heartbeat_factory` preserves the `loop.HeartbeatTimer` injection seam.
- `ToolBatchExecutor(invoker, *, capacity=8).execute(calls, *, prepare, commit,
  observe)` has no queued executor futures. Capacity counts actual still-live
  batch readers, including revoked/timed-out readers. If all eight physical slots
  remain held after their waiting results settle, queued real reads receive
  `tool_capacity`; cancellation yields `tool_cancelled` instead. A later separate
  batch still shares process capacity32. `observe(result)` runs promptly for fast
  completions; `commit(result)` runs once in input order, behind writes/compact/
  unknown serial boundaries. Finally revokes every still-active waiting reader.
- `ReplayDecision(outcome, raw_result='', key=None, recovery_count=0)` and
  `ToolReplayPolicy(owner, *, recovery_limit).decide(tool_def, name, arguments)`
  use existing run-scoped cache sets/maps and canonical identity. Outcomes are
  `execute`, `failed_repeat`, `skipped`, `replayed`, `cached`. Restorations reserve
  their recovery/protection at admission to prevent duplicate restoration while
  awaiting ordered commit. `invalidate_mutable_observations()` clears only mutable
  replay cache/ready/protected sets; deterministic cache remains intact.
- `readonly_replay_allowed(tool_def, arguments)` preserves no_cache, readonly,
  repeatable opt-in and deterministic separation. AgentLoop's existing helper
  wrapper `_readonly_replay_allowed` remains.
- Authorization runs before every replay/cache decision and retains the original
  response's batch symbol/identity snapshot. Cancellation is checked again after
  authorization, so a stop admitted while authorization runs cannot restore a
  payload. Unknown registry entries preserve original registry diagnostic JSON,
  remain serial barriers and have executed=False; they do not expire observations.
- AgentLoop retains `_batch_execute`, `_execute_parallel`, `_execute_single`,
  `_invoke_tool`, tuning/result/compaction helpers and progress class re-exports.
  `_invoke_tool` retains its tuple(raw, elapsed_ms) return and exception propagation.
  `_process_tool_calls` now delegates dispatch and commits executed, synthetic,
  cached, replayed, blocked, cancelled, unknown and compact outcomes in input order.
  Compact/skipped trace records now include their original call_id.
- `RuntimeServices._provider_snapshot()` also replaces abandoned_readers using
  the process-default tool count on **every** aclose return, including already
  closed and foreign-owner paths. It preserves complete, writer/finalizer counts
  and existing loop ownership. True main-owner completion does not claim detached
  readers disappeared.

Controller grounding ruling preserved: raw **executed** results enter GroundingLedger
at the existing single caller commit boundary, before truncation/redaction and in
call order. Cache/replay reuse previously ingested canonical evidence rather than
inventing new external observations; their synthetic call_ids are not fresh grounding
observations/aliases. One ordered terminal transcript/trace record per call_id also
covers all synthetic paths. Grounding identity/authorization policy is unchanged.

## Files in the signed commit

1. agent/src/runtime/agent/tool_execution.py
2. agent/src/runtime/agent/tool_replay.py
3. agent/src/runtime/agent/loop.py
4. agent/src/interfaces/api/runtime.py
5. agent/tests/test_tool_execution_lifecycle.py
6. agent/tests/test_tool_replay_write_barriers.py

Controller README, design/batch clarification and plan checkbox edits remain
unstaged. No docs or other task files were staged. This report is task-local.

## TDD RED evidence

Initial tests required fixing an import path before behavioral verification:
`ModuleNotFoundError: test_readonly_replay_after_microcompact` (two collection
errors). Corrected to the actual agent.tests module; no production code was changed.

Baseline command:

```sh
.venv/bin/python -m pytest agent/tests/test_tool_execution_lifecycle.py agent/tests/test_tool_replay_write_barriers.py -q --tb=short
```

**10 failed, 2 passed in 2.23s**, exit1, before implementation. Log:
`/tmp/task5-red.log`. Expected failures: fast result did not refresh progress before
slow first result; cancellation with timeout disabled kept caller blocked; queued
ninth+ calls could invoke after cancellation; cancelled next result was missing;
ContextVars were lost; [write,replay-ready read] replayed stale content after success,
partial error or exception; denied-write/replay and write/deterministic result order
was wrong. All controlled barriers were released in finally.

Additional behavioral RED → GREEN cycles during implementation/self-review:

- Ordered compact identity: new mixed synthetic/executed test failed with compact
  trace call_id=None. `/tmp/task5-second-red.log`: **1 failed,8 passed in0.95s**.
  Added original compact call_id; subsequent focused36 passed.
- Physical batch slots: `-k timed_out_live` → **1 failed,9 deselected in0.58s**
  (`/tmp/task5-batch-red.log`):16 blocked workers launched rather than8 after timeout.
  Added retained per-batch producer slots and explicit queued capacity results;
  subsequent focused37 passed.
- Bounded progress callback stall: **1 failed,19 passed in1.10s**
  (`/tmp/task5-green4.log`): final 10,000th progress was lost (only first callback
  delivered). Added bounded final caller drain; subsequent20 passed in0.98s.
- Cancellation during authorization: `-k cancel_during_authorization` →
  **1 failed,9 deselected in0.53s** (`/tmp/task5-auth-red.log`): replay returned instead
  of tool_cancelled and reserved recovery. Recheck stop after authorization fixed it.
- Direct unknown-tool helper: `-k unknown_tool_compatibility` →
  **1 failed,10 deselected in0.50s** (`/tmp/task5-unknown-red.log`): unknown registry
  diagnostic expired ready observation. Preserved unknown-helper refusal without
  invoking mutation handling.

First expanded compatibility iteration: **7 failed,381 passed in7.73s**
(`/tmp/task5-cover1.log`). All seven were named micro-compaction identity tests using
real ToolRegistry's unknown padding_tool diagnostic. Unknown refusal was corrected,
not relabeled or weakened in fixtures. Controller approved executed=False semantics.
Next expanded iteration had only the progress-stall failure (**407 passed,1 failed**)
before final drain fix. Core covering run then **409 passed in7.59s**, no warnings.

Expanded facade/DSML compatibility initially **1 failed,466 passed,9 warnings
in9.39s** (`/tmp/task5-final-green.log`):
`test_agent_loop_executes_dsml_textual_tool_call` lacked its test-injected immediate
heartbeat because the extracted module bypassed loop.HeartbeatTimer. Added injected
heartbeat factory from the facade, preserving the original fixture unchanged.

## Final GREEN and static verification

```sh
.venv/bin/python -m pytest agent/tests/test_tool_execution_lifecycle.py agent/tests/test_tool_replay_write_barriers.py agent/tests/test_tool_timeout.py agent/tests/test_readonly_replay_after_microcompact.py agent/tests/test_agent_loop_deterministic_cache.py agent/tests/test_tool_progress.py agent/tests/test_progress.py agent/tests/test_agent_loop_no_progress.py agent/tests/test_agent_loop_stall_watchdog.py agent/tests/test_agent_grounding.py agent/tests/test_grounding_release.py agent/tests/test_compaction_call_identity.py agent/tests/test_technical_indicator_compaction_replay.py agent/tests/test_api_runtime_lifecycle.py agent/tests/test_agent_loop_dsml_tool_calls.py agent/tests/test_agent_loop_thought_signature.py agent/tests/test_agent_loop_no_assistant_prefill.py agent/tests/test_agent_loop_stream_retry.py agent/tests/test_grounding_unknown_call_id_refs.py agent/tests/test_grounding_code_tool_envelope.py agent/tests/test_market_identity_parity.py -q --tb=short
```

**467 passed,9 warnings in9.46s**, exit0. Log `/tmp/task5-final-green2.log`.
Warnings are the nine pre-existing NumPy generic timedelta deprecations in
ccxt_loader.py:39–47; no warning filter or unrelated dependency fix was added.
Final new suites alone: **22 passed in1.12s**, exit0, no warnings
(`/tmp/task5-final-new.log`). New suites cover real process32 retention across32
AgentLoops, two closed/new runtimes and a foreign owner; capacity rejection and
true-exit reuse; thread-start failure release; ContextVars; timeout disabled cancel;
late-progress fencing; actual write ownership; bounded progress flood; raw caller
GroundingLedger order before redaction/truncation; exact synthetic/cancel/compact
call identity; partial failure and persistence failure invalidation; deterministic,
replay and denied/never-started separation.

```sh
.venv/bin/ruff check agent/src/runtime/agent/tool_execution.py agent/src/runtime/agent/tool_replay.py agent/src/runtime/agent/loop.py agent/src/interfaces/api/runtime.py agent/tests/test_tool_execution_lifecycle.py agent/tests/test_tool_replay_write_barriers.py
.venv/bin/python -m py_compile agent/src/runtime/agent/tool_execution.py agent/src/runtime/agent/tool_replay.py agent/src/runtime/agent/loop.py agent/src/interfaces/api/runtime.py agent/tests/test_tool_execution_lifecycle.py agent/tests/test_tool_replay_write_barriers.py
git diff --check
git diff --cached --check
```

Ruff **All checks passed!** Syntax and both diff checks exit0. Only new modules/tests
were Ruff-formatted; no whole-loop formatting. Local Git index writes required the
sandbox's reviewed escalation; staging/commit succeeded for exactly the six files.
No approval rejection, remote operation, push or publishing occurred.

## Self-review, Task 6 handoff and limitations

Self-review read the full changed invocation/policy/facade/runtime paths and covered
bounded admission and progress, worker-exit leases, thread-start failure, actual
context copy, cancel/timeout fencing, authorization timing, write-dispatch mutation
linearization, ordered one-result identity, deterministic/no_cache/repeatable/cap
semantics, unchanged raw grounding and compatibility seams. Corrected unknown refusal,
compact identity, physical slot retention, final progress drain, authorization stop
race and heartbeat injection before the commit. Test teardown now retains and joins
all process-budget callers/workers; start order assertions use actual unordered
worker membership rather than scheduler-dependent thread launch ordering.

Task 6 exact handoff:

- Route `AgentLoop._observe_tool_completion(result: ToolExecutionResult)` through
  captured run generation/lifecycle true progress. It is called only by the caller,
  before ordered commit; current default only refreshes _last_activity_wall for
  executed results excluding tool_cancelled/tool_timeout. Synthetic cache/replay/
  compact/denied/capacity/never-started results cannot become external progress.
- Bind `ToolInvoker.should_cancel` created by `AgentLoop._tool_invoker()` to the
  lifecycle's captured stop predicate. `_process_tool_calls` has pre-batch and
  post-authorization checks plus unknown diagnostic admission checks; `_invoke_tool`
  also checks stop before unknown diagnostics. Task6 must replace these seams
  together, so watchdog stops gate actual tools and caches as well as providers.
- Cancellation outcome/error_code is `tool_cancelled`; timeout is `tool_timeout`;
  exhausted real-reader capacity is `tool_capacity`. A started revoked reader has
  executed=True, but its late success/error is never committed as a new observation.
  An active write keeps owning caller alive until real return; no detached-write
  or force-kill behavior was introduced.
- Preserve ordered raw-result GroundingLedger commit and existing ToolProgress
  observation accounting in `_finalize_tool_result`; its legacy unconditional wall
  activity assignment remains for Task6 to replace with true lifecycle semantics.
- `abandoned_readonly_tool_count()` is the process readonly snapshot API. Runtime
  summary combines it with abandoned_provider_count without redefining complete
  or pending writer/finalizer ownership. Repeated/foreign close continues to refresh.
- Readonly progress/heartbeat callbacks run on caller. Existing write heartbeat and
  timeout-warning timer callbacks remain; Task6's event-generation fence must cover
  those existing timers too. Task5 has not changed terminal claims/watchdog/state
  persistence or request/context accounting.

Limits: Python cannot kill a blocked reader; permanently blocked execution consumes
one process slot until exit. Eight blocked revoked readers consume all slots of that
batch; it reports queued capacity errors instead of secretly starting a ninth.
Cancellation polling is approximately5–10ms plus scheduling; an already-running user
callback cannot be interrupted. Writes remain under main ownership even indefinitely.
Synthetic replay IDs still reuse canonical prior grounding evidence, per controller
ruling. Independent review and Task7 complete integration are pending. No whole
backend/frontend suite, live provider/tool/trading, accounts, secrets, network, remote
mutation, historical confidential objects, or subagents were used.


## Fix round 1 — Important I1, actual readonly dispatch cancellation

Review: task-5-review.md, Important I1 only. Fix base:
`83a84ec5b6edd391a5661b336265426052a753bf`.
Signed local fix commit: **9d4ed87677a834ff70243432b5be38b5d51753f8**
`fix(agent): reject stopped readers at actual tool dispatch`.
Status: DONE_WITH_CONCERNS (independent scoped re-review and original Task7/Task6
integration gates remain controller-owned; no unresolved I1 defect claimed).

### Change and admission semantics

The readonly producer now rechecks its private revocation and captured
ToolInvoker.should_cancel predicate **inside contextvars.copy_context().run(work)**,
immediately before installing the tool progress emitter, setting executed=True,
and invoking registry execution. Snapshot and scheduling delays no longer admit
actual implementation effects after a previously accepted stop. Refusal marks the
reader revoked and leaves executed=False. The same worker finally clears its emitter,
releases its actual process lease, and signals completion on refusal or predicate
failure; caller refusal/timeout still cannot release a live executing producer.

Caller poll additionally honors that producer revocation, so a stop visible only
in the worker's copied request context still returns tool_cancelled rather than
an empty executed result. Predicate exceptions are transported as tool_exception
with executed=False and original exception, and all ownership cleanup still runs.
No public signature, write semantics, replay/cache, raw grounding policy, runtime
summary, terminal/watchdog, provider or context-accounting code changed.

### Behavioral RED → GREEN

```sh
.venv/bin/python -m pytest agent/tests/test_tool_execution_lifecycle.py -q --tb=short -k 'argument_snapshot or worker_admission or worker_stop_predicate_failure'
```

Before production edits: **3 failed,11 deselected in0.53s**, exit1
(`/tmp/task5-fix1-red.log`). Both controlled races failed on measured actual registry
effects: snapshot-copy cancellation still produced ['snapshot']; cancellation after
thread admission but before dispatch still produced ['read effect']. The third case
also produced effects because the worker did not evaluate its stop predicate (which
should have raised in the copied request context). The caller is deliberately held
at its Thread.start seam until worker dispatch resolves, preventing caller polling
from masking the missing boundary by revoking the private reader first. Finally
always opens every barrier and joins every recorded caller and producer.

After implementation: **3 passed,11 deselected in0.43s**, exit0
(`/tmp/task5-fix1-green.log`). These tests assert zero actual effects, executed=False,
exact cancel/error outcome, copied predicate ContextVar value, no progress callbacks
from a refused implementation, and active/abandoned capacity both zero after exit.

Added a fourth named behavior for caller delivery of worker-context-only refusal.
To verify its mutation coverage, temporarily kept the corrected producer check but
restored the prior caller poll condition. Command:
`-k worker_context_stop_refusal` → **1 failed,14 deselected in0.55s**, exit1
(`/tmp/task5-fix1-refusal-red.log`): outcome was executed instead of tool_cancelled,
while the external operation correctly did not run. Restored producer-revocation
observation before the final covering run. This isolated RED specifically proves
why the caller poll change is necessary; it is not represented as a baseline race.

### Final focused covering and static checks

```sh
.venv/bin/python -m pytest agent/tests/test_tool_execution_lifecycle.py agent/tests/test_tool_replay_write_barriers.py agent/tests/test_tool_timeout.py agent/tests/test_readonly_replay_after_microcompact.py agent/tests/test_agent_loop_deterministic_cache.py agent/tests/test_tool_progress.py agent/tests/test_progress.py agent/tests/test_agent_loop_no_progress.py agent/tests/test_agent_loop_stall_watchdog.py agent/tests/test_agent_grounding.py agent/tests/test_grounding_release.py agent/tests/test_compaction_call_identity.py agent/tests/test_technical_indicator_compaction_replay.py agent/tests/test_api_runtime_lifecycle.py -q --tb=short
.venv/bin/ruff check agent/src/runtime/agent/tool_execution.py agent/tests/test_tool_execution_lifecycle.py
.venv/bin/python -m py_compile agent/src/runtime/agent/tool_execution.py agent/tests/test_tool_execution_lifecycle.py
git diff --check
git diff --cached --check
```

**414 passed in7.46s**, exit0, no warnings
(`/tmp/task5-fix1-final-cover.log`). Prior three-case covering iteration was
**413 passed in7.35s** (`/tmp/task5-fix1-cover.log`). Ruff: All checks passed.
Syntax, unstaged/staged diff checks exit0. DCO trailer verified. Only the production
invoker module and lifecycle regression module were staged/committed; no controller
or other task files were included. The original nine ccxt_loader NumPy warnings from
the467-case suite remain tracked, unchanged and unsuppressed; this scoped run does
not claim they were fixed.

Self-review checked stop before executed/effects, copied-context predicate evaluation,
refusal and exception cleanup, caller observation of worker revocation, no late
callbacks, retained lease behavior for already-executing workers, prior start-failure
release, bounded batch/process budgets and exact existing cancellation envelopes.
Task6 handoff now includes the worker-boundary evaluation of its captured lifecycle
stop predicate; it must remain safe to evaluate in the copied request ContextVars.
No new handoff signature or grounding behavior was introduced. No subagents, full
suite, network, live operations, accounts/secrets, remote mutation or push were used.


## Fix round 2 — Important refusal/completion acceptance race

Review: task-5-rereview-1.md. Original actual-dispatch I1 was confirmed ADDRESSED;
this round fixes only the newly observed worker-refusal completion race.
Fix base: `9d4ed87677a834ff70243432b5be38b5d51753f8`.
Signed local fix commit: **37e1e75755dfaeb8b75e45d5b85292c0c2a59fa0**
`fix(agent): preserve reader refusal at completion acceptance`.
Status: DONE_WITH_CONCERNS (fresh independent scoped re-review and original Task6/
Task7 integration remain controller-owned; no unresolved refusal race claimed).

### Explicit refusal terminal state

The private reader has a dedicated refused=False terminal indicator. A worker that
refuses actual dispatch publishes refused=True and private revocation under its
reader gate before the existing finally cleanup signals done. Caller completion
acceptance snapshots refused under that same gate, before applying its own normal
completion-delivery revocation fence. A terminal refusal returns tool_cancelled with
executed=False even if it happened after poll's entry revocation read and the caller
stop predicate remained false. Normal accepted result fencing cannot overwrite the
separate refusal state or manufacture an empty executed result.

The gate protects only private refusal publication/snapshot and existing private
revocation/progress storage. It is never held across should_cancel, registry execution,
progress/event callbacks, diagnostic formatting, or budget/worker cleanup. Actual
results, timeout/cancel envelopes, exception identity, progress drain/fencing, copied
context, true-exit leases, GroundingLedger and public signatures retain the prior
contract. No AgentLoop, replay policy, runtime, provider, write, terminal/watchdog or
context-accounting production code changed.

### Exact controlled RED → GREEN

```sh
.venv/bin/python -m pytest agent/tests/test_tool_execution_lifecycle.py -q --tb=short -k refusal_after_poll_entry
```

Before production edits: **1 failed,15 deselected in0.59s**, exit1
(`/tmp/task5-fix2-red.log`). Test sequence: initial caller predicate admits start;
worker enters its copied-context stop predicate and waits; poll reads revocation
false at entry; its second caller predicate check releases the worker and awaits
actual done while returning false; worker refuses; this same poll accepts completion.
Observed outcome executed, executed=False, empty raw result rather than cancellation.
The assertion failed on the exact misclassified completion outcome, matching the
review probe. Actual registry effects remained absent, so it does not conflate the
already-fixed dispatch bug with this acceptance bug.

After implementation: **1 passed,15 deselected in0.45s**, exit0
(`/tmp/task5-fix2-green.log`). Assertions cover tool_cancelled diagnostic code,
executed=False, exact PreparedToolCall identity, copied predicate ContextVar, no actual
implementation effects or forwarded callbacks, zero active/abandoned capacity and
actual worker exit. Finally always releases the worker and joins every producer.

### Narrowed covering and static checks

```sh
.venv/bin/python -m pytest agent/tests/test_tool_execution_lifecycle.py agent/tests/test_tool_replay_write_barriers.py agent/tests/test_tool_timeout.py agent/tests/test_readonly_replay_after_microcompact.py agent/tests/test_tool_progress.py agent/tests/test_progress.py agent/tests/test_api_runtime_lifecycle.py -q --tb=short
.venv/bin/ruff check agent/src/runtime/agent/tool_execution.py agent/tests/test_tool_execution_lifecycle.py
.venv/bin/python -m py_compile agent/src/runtime/agent/tool_execution.py agent/tests/test_tool_execution_lifecycle.py
git diff --check
git diff --cached --check
```

**76 passed in5.07s**, exit0, no warnings (`/tmp/task5-fix2-cover.log`). Ruff:
All checks passed. Syntax and staged/unstaged diff checks exit0. DCO trailer verified.
Only tool_execution.py and test_tool_execution_lifecycle.py were staged/committed.
Original nine NumPy timedelta warnings remain tracked separately and unsuppressed;
this narrowed run does not claim to fix them. No broader/full suite expansion.

Self-review checked refused publication before done, final refusal snapshot before
own completion fence, read/gate ordering in the exact after-entry interleaving,
independent normal-result/exception behavior, and absence of user callbacks or lease
cleanup under the gate. Task6 public handoff is unchanged: captured stop predicates
still evaluate at actual worker dispatch in copied ContextVars, and the private
reader now preserves its own terminal refusal through caller completion acceptance.
No subagents, live operations, network, accounts/secrets, remote mutation or push.
