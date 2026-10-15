> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 2 implementation report

Status: DONE_WITH_CONCERNS. Task: application resource lifecycle and reliable SSE controls.
Base: `1c864c9972fac0d21ec6871e10df557017e9bcf2`; branch: `codex/architecture-stability-upgrade`.
Signed implementation commit: `56da74615866cbf91533c9a0fba665ea8f20ac94` (`feat(api): own runtime lifecycle and reliable SSE controls`). Only the six listed
production/test files are included; controller plan/design/report files are not staged.

## Implementation and boundaries

- Added `agent/src/interfaces/api/runtime.py`: RuntimeServices owns a four-worker executor,
  injects it into SessionService, serializes lazy construction/retirement, retains
  incomplete services/resources, and releases resources exactly once only after
  the real CloseSummary says complete.
- Updated `agent/src/interfaces/api/state.py`: lazy service composition uses RuntimeServices;
  legacy api_server service/root monkeypatch seams remain at this API boundary.
  Clearing the legacy service host attr rebuilds only an idle service. A live
  owner cannot be evaded by a reset or changed storage-root override. Completed
  close clears service/channel globals and the stopped research executor cache.
- Updated `agent/src/interfaces/api_server.py`: app.state.runtime_services is bound before
  startup. Guaranteed finally ordering stops existing live/channel/scheduler
  inbound drivers before session close, then event/executor resources. Startup
  failure or an inbound shutdown exception still closes initialized session work.
  Existing host lifecycle exports and monkeypatches remain compatible.
- Updated `agent/src/runtime/session/events.py`: subscriber-specific actual running loops;
  publish scheduling and replay/registration are serialized under one gate. No
  worker thread directly mutates asyncio subscriber queues. Data queue capacity
  remains 200; resync, latest terminal and close have independent control slots.
  Runtime shutdown ends streams without pretending their sessions were deleted.
- Added `agent/tests/test_api_runtime_lifecycle.py` and
  `agent/tests/test_event_bus_backpressure.py`: 15 focused cases/parameter variants
  with fake results, actual executor futures, controlled thread Events, independent
  event loops and temporary stores/SQLite. Existing atomic replay characterization
  also passes.

No domain module reads api_server globals. The compatibility reads/reset live in
src.interfaces.api.state and existing API route helpers. No live trading/account/credential/
remote/push/deployment operations; only isolated local loopback/Unix fixtures used
socket permission. No SDK installation or unrelated registry changes were made.

## Shutdown contract and Task 4/5 handoff

`RuntimeServices(sessions_dir, runs_dir)` exposes `executor`, `session_service`,
`closing`, `closed`, `close_summary`, `get_session_service()`, and
`await aclose(timeout_s=5.0) -> CloseSummary`.

The container passes through the exact Task 1 summary fields:
`complete`, `pending_run_writers`, `pending_finalizers`, `abandoned_readers`,
`abandoned_producers`. Same-loop close uses SessionService.aclose. Incomplete close
retains the service, its executor and EventBus; no shutdown(cancel_futures=True),
force-kill claim, same-store recovery, or fresh executor evasion is permitted.
A later lifespan first checks the old close and refuses initialization if pending.
After true complete, bus.close and executor.shutdown(wait=False) run once; new
lifespan gets new service/executor instances. Creation receives the owned executor.

For a different loop, RuntimeServices reads the thread-safe supervisor close
snapshot and stops admission through its gate. It never awaits or cancels old-loop
asyncio tasks. Tests enforce that the service close coroutine itself runs only on
its owner loop, reject a competing lifecycle while a true main future is pending,
and permit rebuilding after the old worker AND finalizer have really completed.
A main future being done is never substituted for CloseSummary.complete.

If an old loop is permanently closed before its continuation/finalizer can finish,
resources remain retained and recovery stays refused. Worker thread exit alone
cannot make that unfinished continuation complete. This deliberate fail-closed
limit is honest; the implementation does not promise to resurrect dead-loop tasks.

RuntimeServices currently passes through Task 1's zero abandoned-reader/producer
counts. Tasks 4/5 must provide their process-scoped, true-exit resource counters for
later composition/integration; zero here is not proof that detached kernel work
was killed. Shared search-index ownership is unchanged (process shared, not an
application-owned per-runtime connection). SessionStore has no close hook. No
cross-scope resource-close helpers were changed.

Cross-loop observation and idle retirement use the existing supervisor gate and
snapshot internally because calling the asynchronous close API from another loop
would touch old-loop tasks. This is narrow API-to-domain composition coupling,
not domain-to-API global access.

## Exact SSE handoff to Task 3

Overflow or an unbuffered replay cursor sends this additive frame:

```text
event: session.resync
data: {"reason":"queue_overflow","latest_event_id":"<latest buffered event ID>"}

```

The alternate reason is `cursor_expired`; latest_event_id is `null` if there is
no buffered event. The control has **no SSE id field**: it must not acknowledge a
continuous sequence of ordinary deltas or replace the client's cursor by itself.
While the control is pending its cursor coalesces to the latest publication seen
before delivery. The control is per subscriber and is not buffered globally.

After either gap, that subscriber stops forwarding all ordinary events/deltas for
its remaining lifetime. It still receives new attempt.completed, attempt.failed,
attempt.cancelled terminals, heartbeat, and session clear. Task 3 must fence its
transient state, read authoritative messages/session/attempt snapshot, reconcile
newer observations, then replace the connection using latest_event_id. Failed
hydration stays unknown/recoverable. Never resend a user POST. Existing terminal
SSE payloads and real terminal event IDs are unchanged.

Control delivery priority is resync, latest retained terminal, then close. On
queue overflow, stale ordinary queue entries are discarded and queued terminal
information is retained in the independent terminal slot. If several unread
terminals require coalescing, the latest is retained and resync explicitly tells
the client to hydrate the authoritative history; older terminal loss is never
silently treated as success. A terminal arriving at a full queue itself triggers
the gap control and remains deliverable. A terminal queued before later delta
flooding is also retained.

`clear(session_id)` retains its existing **session_cleared**, empty data, no-id
SSE control, which immediately terminates that session's stream after pending
resync/terminal controls, even at exactly 200 queued data events. This is session
clear/delete semantics, not application restart semantics.

`bus.close()` is application transport shutdown. It stops bus admission/listeners,
uses an INTERNAL `_stream_closed` sentinel, drains the remaining bounded data queue
and terminals, and ends the iterator. The internal sentinel is never serialized;
no session_cleared deletion frame is emitted. New subscriptions on a closed bus
end immediately. Normal disconnect/reconnect and REST hydration can follow server
restart with fresh tickets. Incomplete runtime close does not call bus.close, so
real finalizers can still publish their outcomes.

`EventBus.set_loop` remains callable for compatibility, but its old injected loop
cannot override a subscriber's captured running loop. The synchronous public
`replay` helper retains its legacy unknown-cursor snapshot behavior; the actual
subscribe/SSE path detects gaps explicitly.

## TDD evidence

Initial command:

```sh
.venv/bin/python -m pytest agent/tests/test_api_runtime_lifecycle.py agent/tests/test_event_bus_backpressure.py -q --tb=short
```

Before implementation: **7 failed in 2.14s**. Failures: session-close absent after
partial startup/inbound failure; missing RuntimeServices; full queue dropped clear
and timed out; overflow returned text_delta instead of resync/terminal; expired
cursor replayed incomplete text_delta; worker-thread queue mutation had the wrong
thread ID; missing bus.close.

Additional RED/green self-review regressions:

- Queued terminal before later overflow: **1 failed, 9 passed in 2.60s** because
  clearing stale deltas also dropped the queued terminal. Fixed by retaining it.
- Cross-loop lifecycle with an assertion that service.aclose runs only on its owner
  loop: **1 failed, 9 passed in 1.77s**, old close coroutine ran on another loop.
  Fixed by using only the supervisor's thread-safe completion evidence there.
- `-k bus_close`: **1 failed, 5 deselected in 0.11s**, app shutdown emitted session
  deletion. Fixed by invisible transport EOF rather than session_cleared.
- `-k legacy_host`: **1 failed, 5 deselected in 1.90s**, clearing the legacy host
  singleton returned its same cached service. Fixed by safe idle retirement/reset.
- `-k runtime_stream_close`: **1 failed, 8 deselected in 0.11s**, transport shutdown
  discarded a queued terminal. Fixed by draining the bounded queue before EOF.

Latest core command is the initial command above: **15 passed in 1.67s**, exit 0,
no warnings. Includes exact-full-200 and 201-event overflow variants, real pending
run/finalizer resource retention, cross-loop refusal and true-completion rebuild,
partial startup failure and exactly-once resources across two lifespans.

## Focused compatibility and environment evidence

Relevant complete pure-memory subset:

```sh
.venv/bin/python -m pytest agent/tests/test_api_runtime_lifecycle.py agent/tests/test_event_bus_backpressure.py agent/tests/test_session_*.py agent/tests/test_api_infrastructure.py agent/tests/test_state_migration_wiring.py agent/tests/test_channel_runtime_reset.py agent/tests/test_channels_runtime.py agent/tests/test_channels_attempt_progress.py agent/tests/test_scheduled_*.py agent/tests/test_api_live_runtime.py -q --tb=short
```

**410 passed, 1 skipped, 9 warnings in 6.82s**, exit 0. The final transport-drain
regression/fix was then verified by the latest 15-core-case command above. The nine
warnings are existing NumPy timedelta DeprecationWarnings in ccxt_loader.py:39-47;
no warning filter was added.

A deliberately wider run additionally included all `test_channel_*.py`,
`test_channels_*.py`, `test_scheduled_*.py`, and `test_websocket_channel_start.py`:
**9 failed, 633 passed, 13 skipped, 9 warnings in 9.72s**. No failure was hidden.
Three socket-bind PermissionErrors were rerun with the controller-authorized
`require_escalated` for ONLY isolated loopback/Unix fixtures:

```sh
.venv/bin/python -m pytest agent/tests/test_channels_config_api.py::test_put_websocket_minimal_section_is_not_a_type_error agent/tests/test_channels_config_api.py::test_put_websocket_form_patch_preserves_requires_token agent/tests/test_websocket_channel_start.py::test_websocket_channel_starts_on_a_unix_socket -q --tb=short
```

**3 passed in 2.26s**. The remaining six failures are:

1. test_channel_authoring_contract.py::test_every_discovered_channel_satisfies_the_authoring_contract[telegram]
2. test_channel_authoring_contract.py::test_declared_noop_keys_are_real_non_secret_non_enabled[telegram]
3. test_channel_config_meta.py::test_unloadable_adapter_is_handled_gracefully
4. test_channel_config_meta.py::test_split_never_leaks_secret_keys_in_values[slack]
5. test_channels_api.py::test_channels_status_reports_all_configured_adapters
6. test_channels_config_api.py::test_post_slack_test_runs_two_leg_probe_not_unsupported

Their BASE counterparts were extracted using git archive
`1c864c9972fac0d21ec6871e10df557017e9bcf2` (sanitized current Task 1 state, not old
confidential history) into `/tmp/vibe-task2-baseline`, including agent/src,
agent/src/interfaces/api_server.py, the four relevant test modules, conftest and pyproject.toml.
The same six exact IDs, with `/tmp/vibe-task2-baseline/agent/tests/` prefixes,
were run together using `.venv/bin/python -m pytest ... -q --tb=short`:
**6 failed in 1.91s**, identically: two missing telegram imports, two Slack field
hint fallback mismatches, Telegram label Destination vs Telegram chat, and Slack
probe unsupported because SDK unavailable. The initial BASE extraction lacked
pyproject.toml; it was added and the final identical run above is the evidence.

Also running just test_channels_api.py on current and BASE produced **1 failed,
3 passed**, same label failure. The controller's full-suite baseline passed; this
isolated behavior may depend on broader collection/shims/registry initialization.
No missing channel SDK was installed to mask it. Root owns the final full suite and
can compare that existing order dependency.

Changed-file Ruff: **All checks passed!** Py_compile for all six files: exit 0.
`git diff --check`: exit 0. No full 17,890-test suite or frontend suite was run by
this worker; Task 7/root owns integration.

## Self-review and concerns

Read the complete six-file diff and reviewed loop capture, replay registration,
control priority/coalescing, closed-loop observation, actual writer/finalizer
retention, legacy resets, partial startup cleanup and exactly-once shutdown.
The extra RED/green cases above came from self-review and the controller's bus-wide
shutdown clarification. No unrelated production path was changed.

Remaining concerns are integration/environment boundaries, not a claim of clean
full validation: isolated channel SDK/order-dependent failures described above;
old dead-loop unfinished finalizers deliberately block recovery; kernel abandoned
resource aggregation awaits Tasks 4/5. Existing API server size and compatibility
globals remain a transitional boundary. Independent review and Task 7 are pending.

## Fix round 1 — independent review I1/I2

Status: DONE_WITH_CONCERNS (original environmental/integration limits retained).
Review: `task-2-review.md`; fix base `e961b950` (only controller documentation
followed Task 2 implementation `56da7461`). Signed fix commit: `493a64bef93ee934f0078f0e3141dbd579160c92` (`fix(api): bind execution loops and bound SSE ingress`).
The two Important findings are addressed together; no full suite or optional
channel SDK suite was rerun, and original 15/410 and BASE environment evidence above
is preserved rather than replaced by the smaller fix verification.

### I1: Actual task ownership determines close permission

Added narrow, read-only `SessionService.execution_loops()` delegating to
`ExecutionSupervisor.execution_loops()` under the existing storage-owner gate.
It reports loops from every unreleased handle's task/execution/finalizer, including
already-done wrappers whose real writer/finalizer still retains the handle.
An unreleased handle without any registered task returns the `None` marker so its
loop cannot be assumed. This cross-scope read-only interface was explicitly
approved by the controller; no Task 1 terminal transaction or release rule changed.

RuntimeServices no longer infers loop ownership from service construction or its
first close caller. It stops admission and snapshots loops/real completion under
one supervisor gate. An actually idle complete owner can release resources. For
unfinished work it calls async SessionService.aclose only when ALL task evidence
belongs to the caller loop. Foreign, mixed, initializing/unproven owners are only
observed; no foreign asyncio task is awaited or cancelled. A later genuine owner
loop close still requests interruption and allows true finalization/release.
Synchronous and separate-idle-loop construction are both covered on a real debug
execution loop with actual executor workers, competing recovery refusal, owner
interruption and subsequent complete/rebuild. The baseline test's guard asserts
before unsafe Future.cancel, preserving clean fixture teardown while reproducing
the exact wrong-loop-close decision from the independent review. An additional
reserved/no-loop fixture proves incomplete ownership/resources stay retained.

Limits remain explicit: if the real owner loop permanently dies before finalization,
close stays incomplete; mixed-loop unfinished handles are conservatively observed
instead of attempting cross-loop cancellation. No worker-exited shortcut was added.

### I2: Bound ingress before the asyncio data queue

Each subscriber now has a locked ingress deque capped at 200 events, independent
pending gap/latest-terminal/close slots, and an atomic scheduled-drain flag.
Publishing updates only that shared ingress state and schedules at most one
pending owner-loop drain callback; callbacks no longer capture each event payload.
The owner loop transfers one bounded batch, mutates its asyncio queue/controls,
and atomically reschedules or clears the flag under the same ingress lock used by
publishers. A concurrent producer therefore either joins the next drain or
schedules it after reset; it cannot strand a terminal/close at that handoff.

Overflow clears/coalesces ingress data immediately, retains the latest terminal,
and latches suppression of ordinary traffic, including later 2,000-event bursts.
A queue-side gap also latches ingress suppression. Pending resync reads the latest
cursor at delivery. Terminal coalescing remains explicit through resync, and close
keeps its independent control slot. Exact session.resync payload/no-id semantics,
session_cleared deletion behavior and invisible runtime shutdown EOF stay unchanged.
Worker threads never mutate subscriber asyncio queues or wake events.

Bounds are per subscriber: 200 ingress events, 200 owner-loop queue events, at most
one 200-event batch currently transferring, and constant-sized control slots; there
is at most one pending drain callback, possibly alongside its running predecessor.
The existing per-session replay buffer remains independently bounded at 500. No
unbounded mailbox substitutes for the callback queue. Two deterministic barrier
variants publish post-gap ordinary traffic, coalesced terminals and clear immediately
before and immediately after the drain flag reset; final control delivery and empty
ordinary ingress prove both wakeup handoffs succeed.

### RED evidence

```sh
.venv/bin/python -m pytest agent/tests/test_api_runtime_lifecycle.py agent/tests/test_event_bus_backpressure.py -q --tb=short -k 'actual_execution_loop or bounded_ingress'
```

Before fixes: **4 failed, 15 deselected in 2.27s**.

- synchronous construction: `AssertionError: foreign loop touched real execution close`;
- idle-other-loop construction: actual owner's interruption was false;
- same-loop flood: **2002 scheduled callbacks > 1**;
- worker-thread flood: **2002 scheduled callbacks > 1**.

These behavioral regressions use actual execution and callback counts plus eventual
control delivery, not just source text or presence of a new private field.
After I1 alone: **2 passed, 6 deselected in 1.78s** for actual_execution_loop.
After both fixes: initial focused core **19 passed in 1.69s** before adding the
handoff and unproven-owner characterization cases.

### GREEN, static checks, and files

Final focused command:

```sh
.venv/bin/python -m pytest agent/tests/test_api_runtime_lifecycle.py agent/tests/test_event_bus_backpressure.py agent/tests/test_session_events.py agent/tests/test_session_events_replay_start.py agent/tests/test_session_execution_lifecycle.py agent/tests/test_api_infrastructure.py -q --tb=short
```

**106 passed in 3.78s**, exit 0, no warnings. Includes original core cases, both
new construction variants, both 2,000-publication contexts, both deterministic
concurrent handoff variants, unproven-owner retention, existing replay/SSE route
coverage, Task 1 execution/finalizer recovery cases and API compatibility seams.

Changed-file Ruff: **All checks passed!** Py_compile for the same six files:
exit 0. `git diff --check`: exit 0.

Changed files (only these are staged in the fix commit):

- agent/src/interfaces/api/runtime.py
- agent/src/runtime/session/events.py
- agent/src/runtime/session/execution.py (read-only loop evidence)
- agent/src/runtime/session/service.py (read-only interface delegation)
- agent/tests/test_api_runtime_lifecycle.py
- agent/tests/test_event_bus_backpressure.py

Self-review checked lock order, mailbox/queue/batch bounds, scheduling flags at
both enqueue/drain handoffs, terminal-before-gap/coalescing preservation, closed
loop detachment, actual task evidence through finalizer completion, conservative
unknown/mixed loop handling and the unchanged public SSE schema. No dependency,
account, live, external server, remote write, push, controller docs staging or
subagent activity occurred. Task 4/5 abandoned-resource aggregation and root's final
integration gate remain outstanding exactly as recorded in the original report.

Post-format focused core: **22 passed in 1.82s**, exit 0, no warnings.
