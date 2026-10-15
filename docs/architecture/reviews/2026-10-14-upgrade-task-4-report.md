> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 4 — Bounded cancellable provider calls

Status: DONE_WITH_CONCERNS (focused verification complete; independent review and
full Task 7 integration remain). Base: ecafe31adf249f0df3584ea2a31c7399081ed2ae.
Signed local commit: **0d70544cad2cca7249456feba312c41a791eff58**
`fix(providers): bound and cancel blocking provider producers`.

## Implementation and interfaces

New `agent/src/runtime/providers/call_runner.py` owns provider producer admission, finite
payload queues, independent terminal signaling, copied context and cancellable
caller waits. Production defaults share one process-owned `ProviderCallBudget(8)`;
creating a new runner, ChatLLM or API runtime does not recreate that budget.
Acquisition fails explicitly without starting or queuing another thread. A lease
stays occupied after caller cancellation/deadline until the actual SDK operation,
producer iterator cleanup, and any deferred owned-client cleanup return. Python
threads are never force-killed. Release bookkeeping runs at the end of the actual
producer cleanup path, not on caller/facade completion.

Interfaces for Tasks 5/6:

- `ProviderCallBudget(capacity=8)`: private injectable capacity; read-only
  `active_count` and `abandoned_count`; shared process budget is used if omitted.
- `ProviderCallRunner(*, budget=None, queue_capacity=8, clock=time.monotonic)`.
  Tests may use a private budget/clock; no environment/config/dependency added.
- `runner.stream(produce, *, should_cancel=None, idle_timeout_s=None,
  deadline=None)` returns a caller-consumed iterator. Producer only returns raw
  chunks/results, never parses, invokes callbacks, writes evidence/state or emits
  events. Queue capacity is finite; one pending producer item and an independent
  terminal/error slot do not compete with payload slots. Queue puts poll revoke.
- `runner.run_call(call, *, timeout_s=None, should_cancel=None)` returns a single
  raw result after producer terminal/cleanup observation. Positive timeout is a
  total monotonic budget; zero/None disable timeout while preserving cancellation.
- `provider_call_scope(*, should_cancel=None, deadline=None)` is a ContextVar
  context manager. Deadline is absolute `time.monotonic()` time. Nested deadlines
  take the minimum and cancellation predicates compose; tokens reset in finally.
  Cancellation predicates are polled by the caller, including while SDK blocks.
- `ProviderCallCancelled` distinguishes stopped waiting from actual SDK exit.
  `ProviderCallCapacityError` is explicit no-capacity admission failure.
- `runner.defer_close(callback)` fences admission/delivery; returns True when an
  active producer (or an existing close request) owns cleanup, False for idle
  immediate cleanup. `runner.close_when_idle(callback)` performs that immediate
  cleanup when appropriate. Owned-client close never closes an iterator blocked
  on another thread. Close-versus-exit selection is atomic; concurrent finishing
  producers retain the final closing lease through external client cleanup.
- `runner.wait_for_idle(timeout)` observes completed producer/cleanup ownership,
  principally for test teardown. It does not claim cancellation force-killed a
  thread. New tests additionally explicitly join every recorded provider thread.
- `abandoned_provider_count()` is a read-only snapshot of revoked, not-yet-exited
  process-budget producers. Private injected test budgets are intentionally absent.

`ChatLLM.chat` and `stream_chat` signatures and LLMResponse remain unchanged.
Messages and tools are deep independent request snapshots; adapter binding and all
SDK operations run on the producer, context copied from the caller. Parsing,
chunk aggregation, DSML suppression, reasoning/text callbacks and provider metadata
handling remain caller-owned. Stream cancel returns accepted partial content (or
empty content) using the existing contract. Errors including idle timeout,
fallback invoke failure and cleanup errors map to existing ProviderStreamError.
One stream attempt uses one lease: empty stream/No generation chunks fallback
invoke executes in the same producer, after its stream iterator cleanup. Revoked
empty streams cannot start that second SDK operation. `chat` propagates cancellation
or total TimeoutError; it has no additional signature argument.

Main streams preserve SDK timeout config and provider idle semantics; no extra
total deadline truncates healthy continuous streaming. Effective arrival is updated
when raw chunks arrive/deliver; caller-induced queue backpressure is excluded from
idle, buffered chunks are consumed without false stall, and tiny chunks preserve
continuity. No producer ever invokes caller callbacks after a wait is revoked.

`AgentLoop` changes are restricted to initial/retry main stream scope binding using
`self._cancel_event.is_set`, and compaction waiting. Standard ChatLLM compaction
calls chat directly within cancellation/total-deadline scope: exactly one lease.
Non-ChatLLM compatibility/test facades get one runner wrapper. No tool admission,
replay, lifecycle terminal logic, watchdog, grounding or ContextMeter was modified.
Task 6 may replace the scope predicate with its lifecycle stop predicate at these
three provider boundaries. Compaction degradation stays caller-owned; late summary
results cannot modify the accepted summary/messages/trace.

`RuntimeServices._provider_snapshot()` narrowly replaces `abandoned_producers` in
CloseSummary and refreshes the stored snapshot on every aclose return, including
already-closed and foreign-loop observation paths. `complete`, pending writers,
pending finalizers and loop ownership retain Task 2 semantics. A main writer may
truly finish with fenced abandoned producers still reported separately. Task 5 can
extend this snapshot with its readonly-reader count; do not interpret `complete`
as disappearance of those retained producer threads.

## Files committed

- agent/src/runtime/providers/call_runner.py (new, 304 lines)
- agent/src/runtime/providers/chat.py
- agent/src/runtime/agent/loop.py (provider scopes + compaction only)
- agent/src/interfaces/api/runtime.py (counter snapshot only)
- agent/tests/test_provider_call_runner.py (new, 20 controlled behavior cases)
- agent/tests/test_chat_llm_streaming.py
- agent/tests/test_anthropic_prompt_cache.py

Two existing characterization assertions were updated to the approved new boundary:
stream should_cancel now uses accepted output rather than number of predicate polls
(because blocked waits must poll too); Anthropic adapter send assertions preserve
identical values but require distinct request identity, since retaining caller list
identity would violate independent snapshots. The pure prompt_cache_messages helper
identity contract is unchanged. Root was informed of both adjustments.
Controller-owned plan/docs were not staged; git status after commit shows only the
controller plan modification. Artifact report is intentionally task-local.

## TDD RED evidence

Command:

```sh
.venv/bin/python -m pytest agent/tests/test_provider_call_runner.py -q --tb=short
```

First collection before a module existed: **1 collection error**
`ModuleNotFoundError: No module named src.runtime.providers.call_runner`.
To obtain behavioral RED, a temporary synchronous baseline scaffold provided only
the proposed imports (`run_call` calls directly, `stream` yields directly, counters
zero, scope no-op); ChatLLM remained the original baseline and ignored injected
runner. This scaffold was replaced entirely by production implementation and was
never committed. Same command: **8 failed, 1 passed in 6.77s**, exit 1:

- first-chunk SDK block: `cancel must return while SDK is still blocked`;
- between-chunk SDK block: same expected prompt-return failure;
- empty-stream invoke block: same expected prompt-return failure;
- direct invoke block with timeout disabled: same expected failure;
- idle before first chunk: old path returned AssertionError from the fake provider's
  bounded guard instead of honoring TimeoutError;
- request snapshots: provider observed mutated messages/tools rather than original;
- cleanup failure: expected `cleanup failed` exception was never observed;
- full queue/cancel boundary: cancellation was never observed by synchronous scaffold.

All controlled waits were bounded; finally released every barrier and joined caller
threads. No permanently blocked test workers were created to demonstrate failure.
The old production ChatLLM blocking/request-snapshot defects are exercised directly
by the first six RED cases; runner-only RED cases used the explicit scaffold.

## GREEN and verification

First covering stream/lifecycle/compaction/runtime iteration: **75 passed**.
Expanded compatibility suite before the deliberate snapshot assertion adjustment:
**1 failed, 151 passed, 9 warnings**; only failure was Anthropic's caller-identity
assertion, contradicting the approved snapshot requirement. After the adjustment:
**152 passed, 9 warnings**. Self-review added two final cleanup/fallback regressions.

Final covering command:

```sh
.venv/bin/python -m pytest agent/tests/test_provider_call_runner.py agent/tests/test_chat_llm_streaming.py agent/tests/test_chat_llm_lifecycle.py agent/tests/test_chat_response_metadata.py agent/tests/test_agent_loop_stream_retry.py agent/tests/test_stream_retry_escalation.py agent/tests/test_swarm_worker_stream_retry.py agent/tests/test_agent_loop_dsml_tool_calls.py agent/tests/test_agent_loop_thought_signature.py agent/tests/test_agent_loop_no_assistant_prefill.py agent/tests/test_openai_codex_replay.py agent/tests/test_anthropic_prompt_cache.py agent/tests/test_auto_compact_chunked_summary.py agent/tests/test_compaction_call_identity.py agent/tests/test_api_runtime_lifecycle.py -q --tb=short
```

**154 passed, 9 warnings in 6.31s**, exit 0. The nine pre-existing warnings are
NumPy generic timedelta deprecations from ccxt_loader.py:39–47 on first import;
no warning filters, dependency installation or unrelated fixes were introduced.
New runner suite alone: **20 passed in 1.96s**, no warnings. Controlled coverage
includes blocked first/between/fallback/invoke, timeout disabled cancellation,
finite queue, cleanup failure, thread-start failure, default shared eight-slot
exhaustion with no extra producer thread, two closed/new runtime lifespans unable
to evade leases, true-exit release, copied context, deep snapshots, callback thread
identity, continuous tiny chunks, slow caller, real/custom compaction cancel and
deadline at capacity one, completed-but-unconsumed close fencing, revoked empty
stream avoiding fallback, and owned-client cleanup retaining capacity.

Static commands:

```sh
.venv/bin/ruff check agent/src/runtime/providers/call_runner.py agent/src/runtime/providers/chat.py agent/src/runtime/agent/loop.py agent/src/interfaces/api/runtime.py agent/tests/test_provider_call_runner.py agent/tests/test_chat_llm_streaming.py agent/tests/test_anthropic_prompt_cache.py
.venv/bin/python -m py_compile agent/src/runtime/providers/call_runner.py agent/src/runtime/providers/chat.py agent/src/runtime/agent/loop.py agent/src/interfaces/api/runtime.py agent/tests/test_provider_call_runner.py agent/tests/test_chat_llm_streaming.py agent/tests/test_anthropic_prompt_cache.py
git diff --check
```

Ruff: **All checks passed!** Compile and diffcheck: exit 0.
Only the new focused runner/test module were Ruff-formatted; legacy large modules
were not reformatted. No whole-backend/frontend run: Task 7/root owns that gate.

## Self-review and limits

Reviewed all seven file changes for cancellation ordering, startup/iterator cleanup
failure, bounded request/payload ownership, capacity release after cleanup, owned
transport deferral, concurrent finishing producers, atomic close-versus-exit,
caller-only callback/parse, DSML/encrypted reasoning/provider metadata preservation,
scope reset, retry compatibility and foreign-loop runtime summary refresh.
Self-review moved release into one failure-safe finish path, fenced completed but
unconsumed streams on owner close, preserved ProviderStreamError around caller
parsing, and ensured async aclose still awaits owned clients for an idle wrapper.

Limits: Python cannot kill a blocked SDK thread. Permanently blocked provider or
cleanup therefore permanently occupies its slot; repeated lifespans cannot evade
it. Cancellation/deadline wait polling is approximately 10ms plus scheduling and
cannot interrupt a callback already executing on the caller. Main stream total
time remains unbounded while effective provider arrivals continue; compaction and
single invoke retain explicit positive total budgets. A custom non-ChatLLM facade
that internally starts its own unrelated runner is outside the standard direct
ChatLLM contract and may create nested admission; standard ChatLLM/subclasses and
repo compatibility facades are covered. Independent review and Task 7 integration
are pending; no live provider, external server, credentials/accounts, trading,
remote operations, push, historical confidential objects or subagents were used.

## Final controller-requested cancellation assertion

Additional signed commit: `e9cb3849` (`test(providers): fence late reasoning after
cancellation`). No production changes. The original four controlled blocking SDK
cases now provide a late reasoning-only chunk followed by late content after the
barrier opens, bind both callback types, assert accepted partial content is kept,
and assert zero late reasoning callbacks after producer exit. This supplements
the already asserted exact accepted text callback list (including no second text).

Focused command after this assertion-only change:
`.venv/bin/python -m pytest agent/tests/test_provider_call_runner.py agent/tests/test_chat_llm_streaming.py -q --tb=short`
**46 passed in 2.21s**, no warnings. Changed-test Ruff: All checks passed; py_compile
and git diff --check exit 0. The full 154-test covering suite was not redundantly
rerun after this test-only clarification; prior production verification above stands.

## Fix round 1 — Important I1, scheduled cleanup after startup failure

Review: `task-4-review.md`, Important I1 only. Fix base:
`d1abd1a437a01226736df1428044bbdf83c86e01`; root-only Task 3 checkbox commit,
production unchanged from Task 4 `e9cb3849`.
Signed fix commit: `a422e87d` (`fix(providers): retain leases through scheduled
client cleanup`). Status: DONE_WITH_CONCERNS (original full integration/review
limits retained; no unresolved I1 gap claimed).

### Change and precise completion handshake

The deferred close callback accepted by ProviderCallRunner.defer_close and
close_when_idle now has type `Callable[[], asyncio.Future[Any] | None]`.
None means synchronous cleanup really returned. A Future means cleanup is pending;
runner finish registers a completion callback and retains the finishing lease,
active/abandoned count and non-idle state until completion.result() succeeds or
raises. Both outcomes release exactly once under the ownership condition lock.
Scheduling is never treated as completion. Startup failure still returns its
original exception promptly; it cannot deadlock the running caller event loop by
synchronously waiting for that loop's cleanup Task.

ChatLLM._run_or_schedule_close now returns its scheduled asyncio.Task, or None if
asyncio.run completed cleanup synchronously outside an event loop. Private
ChatLLM._close_clients returns an asyncio.gather completion Future for all scheduled
owned-client tasks, using return_exceptions=True so one failed client cannot release
ownership while another client remains blocked. Existing per-task failure logging
is preserved. Regular SDK-worker cleanup without a running asyncio loop still
completes synchronously and returns None. Public ChatLLM.chat, stream_chat, close
and aclose signatures/return contracts are unchanged; no runtime/main loop/tool or
controller-owned document changes were made.

### Combined behavioral RED

Added two parameterized behavioral cases to test_provider_call_runner.py combining
actual concurrent owner close, startup failure on a caller running asyncio, and
an owned async client held behind an asyncio.Event. A separate closer thread admits
close while the controlled provider Thread.start is blocked; start then raises.
Finally always releases the async barrier, joins the closer and awaits cleanup.

Command:
`.venv/bin/python -m pytest agent/tests/test_provider_call_runner.py -q --tb=short -k start_failure_retains`
Before production edits: **2 failed, 20 deselected in 0.54s**, exit 1. Both failures
were `active_count == abandoned_count == 1` observing zero while the owned cleanup
had entered and had not exited. This is the exact I1 ownership bug, not an isolated
thread-start or isolated async-close test.

After implementation: **2 passed, 20 deselected in 0.47s**, exit 0. The failure case
was then strengthened with an additional owned client that fails immediately while
the original cleanup is still blocked, verifying all-client completion semantics.
The covered cases assert startup exception identity, cleanup actually pending,
occupied/abandoned budget and non-idle state while pending, capacity rejection,
release after true cleanup success/failure, and successful reuse of the freed slot.

### GREEN covering suites and static checks

```sh
.venv/bin/python -m pytest agent/tests/test_provider_call_runner.py agent/tests/test_chat_llm_streaming.py agent/tests/test_chat_llm_lifecycle.py agent/tests/test_chat_response_metadata.py agent/tests/test_api_runtime_lifecycle.py -q --tb=short
.venv/bin/ruff check agent/src/runtime/providers/call_runner.py agent/src/runtime/providers/chat.py agent/tests/test_provider_call_runner.py
.venv/bin/python -m py_compile agent/src/runtime/providers/call_runner.py agent/src/runtime/providers/chat.py agent/tests/test_provider_call_runner.py
git diff --check
```

**65 passed in 3.62s**, exit 0, no warnings. Ruff: **All checks passed!** Compile and
diffcheck: exit 0. No unrelated/full suite expansion; the original nine ccxt_loader
NumPy warnings from the earlier 154-test suite remain explicitly recorded above
and are not suppressed or declared fixed.

Files committed: agent/src/runtime/providers/call_runner.py, agent/src/runtime/providers/chat.py,
agent/tests/test_provider_call_runner.py only. Self-review checked synchronous
cleanup, startup failure on a running loop, all-client failure completion,
registration before lease release, no lock held across external cleanup, callback
result/failure release and unchanged idle-wrapper async aclose behavior. Pending
cleanup on a dead/stopped event loop conservatively retains ownership rather than
pretending completion. Independent scoped re-review and Task 7 remain root-owned.
