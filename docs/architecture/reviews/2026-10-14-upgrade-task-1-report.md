> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 1 implementation report

Status: DONE_WITH_CONCERNS. Task: session execution ownership and durable outcomes.
Baseline: `67b4ba05da9b89f6e70d37980e613edbc756ba6c` on
`codex/architecture-stability-upgrade`. Implementation is in the signed task commit
titled `feat(session): supervise execution and durable outcomes`; its resolved SHA
is recorded below after committing. The local report is separate from the staged
production/test patch, as requested by the controller.

Task 1 commit: `7f6f980e8f90660676d591eb6b019e5ef0a3d675`
(`feat(session): supervise execution and durable outcomes`), with the repository's
DCO `Signed-off-by:` trailer. This commit contains only the six listed production/
test files; the controller's design/plan changes remain unstaged.

## Changes

- Added `agent/src/session/execution.py`: identity handles, a storage-owner gate,
  bounded admission, actual executor futures, and monotonic OPEN/CLOSING/CLOSED state.
- Updated `agent/src/session/service.py`: supervised wrappers/execution/finalizers,
  transcript-first terminal persistence, restart evidence adoption, safe optional
  projections, cancellation identity checks, close and attempt snapshot interfaces.
- Updated `agent/src/session/store.py`: idempotent linked terminal append, JSONL tail
  repair and commit delimiting, actual deletion errors instead of ignored failures.
- Updated `agent/src/session/search.py`: transactional FTS deletion, authoritative
  store existence filtering, serialized operations on the shared SQLite connection.
- Updated `agent/src/api/sessions_routes.py`: busy deletion 409, closing/capacity 503,
  optional conditional-cancel JSON, and an authenticated read-only attempt snapshot.
- Added `agent/tests/test_session_execution_lifecycle.py`: 30 regression cases using
  temporary filesystem/SQLite stores, fake providers and controlled Events/threads.

No runtime/lifespan container or Agent kernel changes, production dependencies,
accounts, credentials, remote mutations, live trades, push or deployment.
Controller-owned design/plan/brief changes were not staged by this task.

## RED evidence

1. `.venv/bin/python -m pytest agent/tests/test_session_execution_lifecycle.py -q --tb=short`
   initially produced **17 failed in 2.86s** on the unchanged service. Failures
   exposed immediate cancel stranding ownership, missing conditional cancellation,
   active deletion, cleared events after failed deletion, optional index/event/
   checkpoint failures rewriting completed as failed, lost recovery evidence,
   missing close/owner retirement, actual worker overlap after cancelled wrapper,
   undelimited JSONL acceptance/tail handling, stale deleted search hits and missing
   transactional index deletion. Some additive interface cases failed because the
   required method/keyword did not yet exist; behavioral cases failed on actual
   stored state, search results or admission.
2. The same file with `-k 'bounded_admission or rest_active'` produced **2 failed,
   17 deselected in 1.89s** before the new constructor/body/REST implementation.
3. A sanitized current-HEAD baseline was extracted with
   `git archive HEAD agent/src agent/api_server.py agent/tests/conftest.py | tar -x -C /tmp`;
   the regression file was copied to `/tmp/agent/tests/`. Running
   `.venv/bin/python -m pytest /tmp/agent/tests/test_session_execution_lifecycle.py -q --tb=short -k 'snapshot or share_one or old_callbacks'`
   produced **3 failed, 19 deselected in 2.14s**. The old callback regression was
   refined to reach its behavioral assertion instead of a missing private handle;
   rerunning with `-k old_callbacks` produced **1 failed, 21 deselected in 0.62s**:
   stale callbacks recreated a released attempt's checkpoint.
4. Baseline extraction with `-k uncertain_reply` produced **1 failed, 24 deselected
   in 0.09s**: a committed reply followed by an append exception became a failed
   attempt instead of recoverable running evidence.
5. During implementation, `-k 'cancelled_finalizer or finalization_owns'` produced
   **2 failed, 23 deselected in 2.72s**: cancelling a deferred finalizer stranded
   the claim, and cancellation was falsely admitted after finalization began.
   Both started and not-yet-started finalizer cancellation are now covered.
6. `-k 'valid_repaired_tail or stable_identity'` produced **2 failed, 26 deselected
   in 0.15s**: recovery ignored the completed record adopted by tail repair, and
   terminal candidates changed identity before their first successful commit.
7. `-k slow_terminal_io` produced **1 failed, 28 deselected in 2.24s** before
   offloading the terminal transaction: synchronous append blocked the loop and
   close could not expose its pending finalizer.
8. `-k concurrent_index` produced **1 failed, 29 deselected in 0.18s** before
   serializing index transactions: another thread's index commit made a failing
   delete irreversible, losing a surviving session's FTS evidence.

The temporary baseline is the sanitized current commit, not removed historical or
confidential code. All tests use the repository conftest runtime sandbox.

## GREEN and checks

Final focused command:

```sh
.venv/bin/python -m pytest agent/tests/test_session_*.py agent/tests/test_api_infrastructure.py agent/tests/test_security_auth_api.py agent/tests/test_attribution_api.py agent/tests/test_goal_api.py agent/tests/test_api_live_runtime.py agent/tests/test_mcp_goal_session_contract.py agent/tests/test_mcp_goal_session_isolation.py -q --tb=short
```

Result: **277 passed, 9 warnings in 7.50s**, exit 0. This includes all existing
session/store/search/checkpoint/recovery suites and relevant API/MCP/live-runner
compatibility tests. No warning filters were added. The nine warnings are existing
NumPy timedelta DeprecationWarnings from `agent/backtest/loaders/ccxt_loader.py:39-47`.

After finalizer offload, the complete relevant lifecycle/recovery/metadata/trail/MCP/
search subset also passed: **76 passed in 2.90s** before adding the index concurrency
case. The final 277 run covers that additional fix.

After explicit exception re-export and unused-import cleanup, the new regression
file was checked again: **30 passed in 2.33s**, exit 0.

`.venv/bin/python -m py_compile agent/src/session/execution.py agent/src/session/service.py agent/src/session/store.py agent/src/session/search.py agent/src/api/sessions_routes.py agent/tests/test_session_execution_lifecycle.py`
and `git diff --check`: both exit 0.

Ruff was attempted for the touched files but is unavailable in the existing virtual
environment: `No module named ruff`; no dependency was installed to hide this omission.

The worker did not run the full backend or frontend suite. The controller's unchanged
baseline was **17,890 passed / 85 skipped / 2,097 pre-existing warnings in 210.48s**
(`/tmp/vibe-upgrade-backend-baseline-complete.log`); frontend **77 files / 744 passed**.
The integration gate remains controller-owned.

## Interfaces and shutdown handoff to Task 2

`SessionService(store, event_bus, runs_dir, *, executor=None, max_inflight=16)` uses
the injected executor or the existing `_AGENT_EXECUTOR` seam. Admission capacity
covers all reserved/executing/finalizing attempts; same-session admission is 409,
capacity or closed admission is `SessionServiceClosingError` mapped to REST 503.
Rejection precedes user-message and attempt persistence. Create/send/delete mutations
share the storage owner gate. The supervisor does not shut down an injected executor.

`await service.aclose(timeout_s=5.0) -> CloseSummary` stops admission first, requests
cooperative service interruption, and waits up to the supplied deadline. Repeated
calls are idempotent. Timeout retains CLOSING and the owner; actual writer/finalizer
exit and release transition to CLOSED. The dataclass fields are:

- `complete: bool`
- `pending_run_writers: tuple[str, ...]` (attempt IDs, including registry/preparation
  futures and an execution continuation that has not yet finished)
- `pending_finalizers: tuple[str, ...]` (attempt IDs whose execution exited and
  terminal transaction/continuation remains owned)
- `abandoned_readers: int = 0`, `abandoned_producers: int = 0` (Task 1 does not claim
  to count or clear kernel producer/read leases; RuntimeServices must aggregate them)

Compatibility views `_active_tasks` and `_inflight` remain. `_active_tasks` is the
outer wrapper, not proof of actual exit. The identity handle is stored in
`service._supervisor.handles[session_id]`: `execution` is the shielded continuation,
`workers` retains actual preparation/main `concurrent.futures.Future` objects,
`finalizer` is the separately scheduled continuation when a wrapper was cancelled,
and `finalization_future` owns the entire terminal I/O transaction. Wrapper or
finalizer cancellation never cancels or releases a live actual worker/I/O future.
Release checks handle identity, execution exit, every real future exit and
finalization completion/recoverable error. Old callbacks cannot release another
attempt or write late checkpoints/events.

User cancel before main dispatch commits a cancelled assistant record, including
immediate cancellation before the wrapper body executes. A user cancel after the
main worker starts requests `AgentLoop.cancel()` and adopts that worker's unique
outcome. Raw wrapper cancellation or `aclose()` is service interruption: preparation
leaves pending/running evidence for restart; a cooperative cancelled main outcome
also remains recoverable as interruption; an actual success/failure main outcome is
persisted from the worker rather than fabricated from `CancelledError`. If an actual
write never returns, the owner stays active and close reports unfinished work.

Registry and main dispatch run under the supervisor identity/cancel gate. Dispatch
before a main start can be refused without launching AgentLoop. The kernel already
preserves first-run pre-start cancellation with its `_has_run` guard; Task 1 does not
change AgentLoop or claim stronger guarantees for deliberate reuse of its instance.

Same-store ownership is keyed by resolved `store.base_dir`. A constructor acquires
the owner before recovery. A live old main worker or finalizer refuses construction
with `SessionServiceClosingError`. Replacing an idle owner atomically retires its
supervisor to CLOSED; the old object cannot admit work later. Strong registration
retains live owners; idle registrations are weak. Runtime must retain an incomplete
closing service and executor instead of initializing another same-store writer.

## Durable outcomes and persistence failure handoff

Terminal candidates use a stable UUID-derived message ID for the attempt. Complete
newline-delimited JSONL assistant evidence is authoritative; a final physical line
without its delimiter is not returned by normal history parsing. Append repair adds
a delimiter to isolate original tail bytes, rechecks linked evidence, adopts a valid
complete prior terminal record exactly once, and otherwise appends the new record
without discarding a damaged fragment. Recovery uses the adopted reply's status and
metrics, avoiding an interrupted/completed disagreement after repair.

The whole final checkpoint/transcript/attempt transaction is one supervised executor
job, preserving its order. The final text is checkpointed best-effort first; the
linked reply is appended before terminal attempt.json. If the transcript append
fails before commit, the persisted attempt remains pending/running with checkpoint
evidence for interrupted recovery. If the append succeeded but acknowledgement or
attempt update failed, the complete linked terminal reply is sufficient for exactly
one terminal recovery. `handle.persistence_error` records the finalizer exception;
release is permitted only after this recorded recoverable failure and real I/O exit.
Checkpoint cleanup, index, and notification errors after terminal attempt commit do
not rewrite status or delete committed transcript evidence. No uncertain user send or
external write is automatically retried. Store deletion failures propagate; events
are only cleared after successful filesystem deletion. Search deletion is transactional
and stale projection rows are filtered against the authoritative bound store.

## Conditional cancel and snapshot handoff to Tasks 2/3

`service.cancel_current(session_id, expected_attempt_id: str | None = None) -> bool`:
comparison and admission are atomic under the owner gate. A mismatched or terminal/
finalizing identity returns false. The REST cancel route accepts optional JSON
`{"expected_attempt_id": "..."}`; no-body clients keep legacy behavior. Accepted
returns `{"status":"cancelled"}`, mismatch/inactive returns
`{"status":"no_active_loop"}`. Acceptance does not imply actual worker exit.

`GET /sessions/{session_id}/attempts/{attempt_id}` is authenticated and validates
both path segments; missing session/attempt returns 404 with a string `detail`.
It calls `service.get_attempt_snapshot(...) -> dict | None` and returns:

```json
{
  "attempt_id": "string",
  "status": "pending|running|waiting_user|completed|cancelled|failed|interrupted",
  "started_at": null,
  "terminal_metadata": {}
}
```

`started_at` is a number (epoch seconds) when known, otherwise null. A complete
linked terminal reply wins over a stale attempt.json. `terminal_metadata` only
contains available `elapsed_ms` (number), `started_at` (number), `run_id`, `provider`,
`configured_model`, `model`, `model_source`, `reasoning_effort` (strings from existing
runtime metadata). Nonterminal state returns `{}`. It does not expose prompt,
credentials, arbitrary metadata, transcript text or absolute run directory paths.
The old SessionResponse success DTO is unchanged. Hydration must use this authority
for running/pending and empty-content terminal states rather than infer success from
absence of an assistant message.

## Self-review and remaining concerns

Reviewed the complete touched production diff, identity release paths, same-store
retirement, wrapper/finalizer cancellation (including before first coroutine step),
terminal fault cases, tail repair, route status/body compatibility, thread-safe index
transactions and context propagation into actual execution futures. The additional
RED/green finalizer, repaired-tail and shared-connection cases came from this review.

Remaining limits: synchronous short create/send/delete/startup/recovery filesystem
operations and checkpoint flush/client cleanup at the facade boundary retain their
existing synchronous API; this change does not claim all storage I/O is nonblocking.
The process-local registry is not a multi-process filesystem transaction or writer
lock. Thread force-kill, power-loss durability beyond atomic JSON/complete JSONL
evidence, kernel detached producer/read fencing, and event subscriber lifetime are
outside Task 1. Runtime must not close its event resources/executor or discard an
incomplete owner while real write/finalizer futures remain. The independent task
review and controller integration gates have not yet run.

## Fix round 1 — independent review I1

Signed fix commit: `1c864c9972fac0d21ec6871e10df557017e9bcf2`
(`fix(session): retain ownership during startup recovery`). Only the three Task 1
production/test files below were staged; the report remains controller-managed.

Review base: `eb6634176a4306beb37298430000b1cfcfe8c689` (the initial Task 1
implementation plus controller-only documentation). The independent review found
that constructor recovery was an unregistered writer: another SessionService with
another SessionStore object for the same directory could retire that recovering
owner between its idempotency lookup and append, producing two terminal records.

Changed only `agent/src/session/execution.py`, `agent/src/session/service.py` and
`agent/tests/test_session_execution_lifecycle.py`. The supervisor now registers an
`initializing` owner and retains a strong registration before startup resource
initialization or any recovery write. Replacement checks initializing ownership as
well as execution handles. All resource initialization/recovery is covered by the
constructor's try block. `finish_initialization()` releases startup ownership only
after the synchronous recovery/resource stack actually returns or unwinds; failure
marks CLOSED and releases its strong registration, permitting a later recovery.
An explicit close request during a blocked recovery stays CLOSING and reports
`complete=False`; it cannot falsely close or replace the live recovery writer.
This extends the existing single-store owner guarantee to startup writes without
changing runtime/lifespan, AgentLoop, EventBus or public success DTOs.

New regressions use two distinct SessionStore instances for one temporary directory
and pause recovery after terminal idempotency lookup (or at attempt update). They
cover normal recovery, failed reply append, failed attempt commit, and startup index
resource failure. Each competing constructor must be rejected while the first
stack is live; after it exits, another constructor can recover/admit work, and the
attempt has exactly one terminal record. The normal case also explicitly closes
the still-recovering service and verifies CLOSING/incomplete ownership remains.
All thread/Event barriers release in finally blocks.

RED command:

```sh
.venv/bin/python -m pytest agent/tests/test_session_execution_lifecycle.py -q --tb=short -k 'same_store_recovery or initialization_resource_failure'
```

Result before the fix: **4 failed, 30 deselected in 2.26s**. All three recovery
variants admitted the competing constructor; resource initialization admitted it
into the blocked resource factory instead of refusing it. After the fix: **4
passed, 30 deselected in 0.14s**. The explicit close assertion was added to the
normal barrier case before the final complete focused run.

Final GREEN command:

```sh
.venv/bin/python -m pytest agent/tests/test_session_execution_lifecycle.py agent/tests/test_session_restart_recovery.py agent/tests/test_session_service_lifecycle.py agent/tests/test_session_store_*.py agent/tests/test_session_search.py agent/tests/test_session_service_mcp.py agent/tests/test_session_reply_runtime_metadata.py agent/tests/test_session_tool_trail.py -q --tb=short
```

Result: **93 passed in 2.99s**, exit 0. No warnings were emitted by this focused run. Changed-file Ruff:
`.venv/bin/python -m ruff check agent/src/session/execution.py agent/src/session/service.py agent/tests/test_session_execution_lifecycle.py`
→ **All checks passed!** `git diff --check` → exit 0. Ruff is now installed by the
controller; this resolves the initial report's environment omission. No warning
filter or unrelated loader change was added; the nine existing warnings remain
recorded for the integration controller.

Self-review checked registration-before-resource-init, rejection-before-retirement,
strong-owner retention, exception cleanup after actual exit, close state during
recovery, and usable exactly-once recovery after either intermediate storage fault.
No additional design question or new scope was introduced. The process-local owner
and remaining synchronous startup/short-I/O limitations remain as stated above.
The scoped independent re-review and integration gates are pending.
