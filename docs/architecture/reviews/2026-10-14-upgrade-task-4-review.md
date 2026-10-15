> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 4 independent review — ecafe31a..e9cb3849

## Spec Compliance

- ❌ One verified requirement gap: asynchronous owned-client cleanup can outlive its released budget lease on the thread-start-failure path (`agent/src/runtime/providers/call_runner.py:227`, `:232`, `:274`; `agent/src/runtime/providers/chat.py:473`). See Important I1.
- ✅ The reviewed two-commit diff otherwise implements the requested boundary: process budget 8 (`call_runner.py:69`), bounded payload queue and separate terminal signal (`:204`), cancellation/idle/total-deadline observation (`:180`), copied context and independent request snapshots (`chat.py:498`, `:547`), caller-owned parsing/callbacks, one-lease empty/no-generation fallback with revoke check (`chat.py:570`), direct standard-ChatLLM compaction plus one compatibility wrapper (`loop.py:2653`). No tool/watchdog/context/lifecycle rewrite was introduced.
- ✅ `RuntimeServices._provider_snapshot()` only replaces the producer count and is reached on each aclose return (`runtime.py:66`, `:87`, `:99`, `:103`, `:108`, `:113`); the diff leaves Task 2 main-worker/finalizer completion semantics intact. The unchanged exact handoff is documented in `task-2-report.md:43`–`:69`.
- ⚠️ Cannot verify from this task: Task 5 reader accounting/admission and Task 6 terminal-owner/lifecycle integration remain later work. Full backend/frontend/build/gates remain Task 7. Real SDK/transport behavior is not proved by fake-provider tests; no live accounts or requests were used. Reported 154-pass and final 46-pass runs were read as supplied evidence, not independently rerun.

## Strengths

- The producer confines itself to SDK work and raw results; caller cancellation closes the delivery iterator, preventing blocked first-next/next/fallback/invoke output from reaching text or reasoning callbacks. The controlled phase test retains accepted partial text and checks no late reasoning after producer exit (`test_provider_call_runner.py:48`).
- Capacity is shared across runner and API lifespan instances, and admission rejects before creating another worker. The saturation test checks all eight outstanding leases, two lifespans, and exact worker count (`test_provider_call_runner.py:291`).
- Queue backpressure is excluded from provider idle timing, while buffered output precedes idle checks. Cleanup is performed by the producer rather than synchronously closing a generator blocked in another thread (`call_runner.py:235`).
- Compaction keeps its total budget, preserves cancellation with timeout zero, and avoids nested admission for ordinary ChatLLM (`loop.py:2653`; controlled one-slot tests at `test_provider_call_runner.py:366`).
- Public chat/stream signatures and LLMResponse are preserved. Existing metadata/DSML/thought-signature/encrypted reasoning parsing stays caller-owned; prompt-cache adapter assertions correctly change identity to independent equal-content snapshots while the pure helper contract is unchanged (`chat.py:487`, `:511`; `test_anthropic_prompt_cache.py:164`).

## Issues

### Critical

- None found.

### Important

- **I1 — Deferred async cleanup releases ownership early after thread startup failure.** `agent/src/runtime/providers/call_runner.py:274` executes `finish()` on the caller when `thread.start()` raises. If an owner-close request was admitted while startup was in progress, `finish()` invokes its deferred callback at `:227`, then releases the lease at `:232`. On a caller with a running asyncio loop, the callback `ChatLLM._close_clients` schedules awaitable client cleanup through `agent/src/runtime/providers/chat.py:473` and returns immediately. The budget and `wait_for_idle()` therefore claim completion while owned cleanup remains blocked, violating the explicit cleanup/true-exit budget requirement. Keep this lease until the scheduled async cleanup actually completes, including failure; make the cleanup completion handshake explicit rather than treating scheduling as completion. Add one bounded regression combining startup failure, concurrent/deferred owner close, and async cleanup under a running event loop. Current tests at `test_provider_call_runner.py:255` and `:473` cover the two cases separately and miss their interaction.

### Minor

- **M1 — Existing warning noise remains.** The supplied covering-run evidence (`task-4-report.md:151`) records nine NumPy generic timedelta deprecation warnings from `ccxt_loader.py:39`–`:47`. These are explicitly reported as pre-existing, not caused by this patch. Keep them named at the Task 7 gate; do not add suppression merely to make the output clean.

## Checks and limits

- Read the complete 1,544-line two-commit diff once in sequential slices. No checkout/index/HEAD operations or production/test edits, no full-suite rerun, no subagents, no secrets/live/remote operations.
- Named unchanged dependency check: async cleanup scheduling could escape lease ownership. Inspected `_close_candidates` and `_run_or_schedule_close` in `chat.py:440`–`:485`, along with the aclose function tail that was cut off by the diff hunk. This revealed I1.
- Named cross-task handoff check: producer counts must not redefine true main/finalizer completion. Read the relevant Task 2 report contract and compared all changed runtime returns; no additional mismatch found.
- One focused, transient Python probe (no saved test/source edits): inside `asyncio.run`, inject a private capacity-one runner and owned async client whose `aclose` waits on an Event; replace Thread.start with a controlled function that requests client.close then raises. This reproduces the admitted-close/start-failure interleaving without launching a worker. Output: `call_error: start failed`, `cleanup_pending: True`, `budget_active_while_cleanup_pending: 0`, `runner_reports_idle: True`. Restored Thread.start and released/awaited cleanup in finally. Exit 0; no warnings.

## Assessment

**Task quality: Needs fixes.** The main cancellation and bounded producer design is well isolated and strongly exercised. I1 is a narrow but demonstrated violation of the explicitly required cleanup ownership guarantee; fix and verify that combination before approving Task 4.
