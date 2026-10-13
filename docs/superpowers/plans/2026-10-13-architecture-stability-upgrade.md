# Architecture Stability Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make session execution, streaming UI and the Agent kernel stable under cancellation, concurrency, partial failures and shutdown.

**Architecture:** Retain the existing framework and external contracts while introducing explicit ownership at session, transport, provider, tool and run boundaries. Separate authoritative state commits from optional projections and separate worker completion observation from ordered evidence commits. Use six bounded implementation tasks plus an integration gate.

**Tech Stack:** Python >=3.11, FastAPI, LangChain provider adapters, filesystem/SQLite, React 19, TypeScript, Vite, Zustand, pytest, Vitest.

**Spec:** `docs/architecture/2026-10-13-stability-design.md`

## Global Constraints

- Implement the spec's Compatibility and constraints section verbatim: preserve API/tool/event/public facade contracts, no new production framework/database/dependency, no real accounts/credentials/orders or remote publishing.
- Never recover the removed confidential strategy or its old Git objects. Baseline `c25fea2bc261a2d0ed971c989d194de9f04ec963`; branch `codex/architecture-stability-upgrade`.
- Workers may edit only their listed task files and coherent new modules/tests in that boundary. Coordinate any shared-file exception with the controller before editing.
- Never run implementation workers in parallel. Root owns integration tests and cross-task docs; use fresh task reviewers and signed local commits (`git commit -s`).
- Write regression tests first, record the expected baseline failure, then implement. Tests assert observable behavior with isolated state and fake external operations.
- Reports include commands, exit/results, concerns and commit SHA. Full suites are run once at Task 7; workers run focused covering suites, avoiding duplicate full-suite work.
- Existing private-method/re-export compatibility used by tests remains during migration. Do not weaken safety/grounding or suppress a failure merely to make tests green.

## Review Focus

- Immediate cancel before coroutine first execution: persisted cancelled terminal and reusable session.
- A→B→A navigation and same-session next attempt: old asynchronous callbacks do not mutate new state.
- Header success followed by stalled body/iterator: deadlines remain effective and resources remain bounded.
- Partial-effect error write or denied write: mutable replay invalidates only when real execution happened.
- Shutdown racing durable terminal/recovery: one terminal result, no old writer after ownership release.

---

### Task 1: Session execution ownership and durable outcomes

**Files:** Create `agent/src/session/execution.py` and, if needed, `agent/src/session/finalization.py`; modify `agent/src/session/service.py`, `store.py`, `search.py`, `agent/src/api/sessions_routes.py`; tests in `agent/tests/test_session_execution_lifecycle.py` and related existing session/search suites.

**Interfaces:** Consume existing SessionStore/EventBus/Attempt models. Produce an execution supervisor that reserves, starts, cancels and releases an attempt by identity; retain underlying worker futures until actual exit. Expose `SessionService.aclose(timeout_s)` for Task 2 and a service-closing exception that routes map to 503. Retain public create/send/cancel/delete/get methods and legacy test handles as compatibility views where necessary. Add optional expected_attempt_id to cancellation and optional REST JSON body; atomically compare before admitting cancel, mismatch returns existing no-active status, no-body clients stay compatible.
Add `SessionService.get_attempt_snapshot` and authenticated GET `/sessions/{sid}/attempts/{attempt_id}` for pending/running/empty-terminal authoritative recovery. Only safe status/timing/runtime metadata; committed linked terminal evidence wins, no prompt/credentials/absolute paths. Default in-flight admission capacity is 16, constructor overridable.

- [x] Step 1: Regressions: immediate cancel without yielding → cancelled persisted, no stuck claim, next send admitted; old completion cannot release new attempt; delayed conditional cancel for attempt A cannot cancel attempt B in the same session; actual worker ownership outlives cancelled wrapper; active delete raises SessionBusyError/REST 409; delete/send race is linearized; closed admission refuses without durable side effects.
- [x] Step 2: Record baseline RED for `test_session_execution_lifecycle.py` using `.venv/bin/python -m pytest ... -q --tb=short`. Isolate providers and real filesystem state.
- [x] Step 3: Implement the supervisor and service integration. Keep user cancel distinct from service interruption. Implement transcript-first idempotent terminal persistence and isolate search/notify failures after durable commit; retain recoverable evidence after intermediate storage failure. Add transactional FTS session deletion and filter missing sessions on search paths as necessary.
- [x] Step 4: Add fault injection for reply/attempt/checkpoint/index/event phases, deletion I/O failure, deleted search results and recovery without duplicate replies. Run existing session service/store/search/checkpoint/cancel/status/API suites plus new tests; record results.
- [x] Step 5: Self-review and signed task commit; report exact interface semantics and shutdown behavior for Task 2.

### Task 2: Application resource lifecycle and reliable SSE controls

**Files:** Create `agent/src/api/runtime.py` if needed; modify `agent/api_server.py`, `agent/src/api/state.py`, `agent/src/session/events.py`, shutdown hooks only as needed; tests `agent/tests/test_api_runtime_lifecycle.py`, `agent/tests/test_event_bus_backpressure.py` and existing lifecycle/events suites.

**Interfaces:** Consume Task 1 `SessionService.aclose(timeout_s)`. Produce RuntimeServices ownership at the API boundary, restart-safe close/reset, and loop-owned SSE subscriber resources. Keep legacy api_server exported symbols and test monkeypatch seams compatible. No domain code may begin reading api_server globals.

- [x] Step 1: Regressions: full 200-event subscriber queue still receives session close and terminates; terminal cannot silently vanish; worker-thread publish targets the subscriber's actual loop; replay registration stays atomic; startup/partial failure/shutdown order closes resources once; next app lifespan never reuses an unclosed writer.
- [x] Step 2: Record baseline RED with focused lifecycle/event tests.
- [x] Step 3: Bind RuntimeServices to app lifecycle and lazily preserve current entry points. Stop inbound scheduler/channel work before session close; report incomplete worker shutdown honestly, retain closing same-store owner until actual worker exit. Guarantee control delivery under backpressure without inventing successful outcomes; additive session.resync carries reason and latest buffered cursor and triggers REST hydration in Task 3.
- [x] Step 4: Run event bus, session API, startup/shutdown, scheduled/channels integration suites in isolated temp state. No externally reachable server; loopback fixtures only when explicitly needed.
- [x] Step 5: Signed commit and report; specify any additive SSE control for Task 3.

### Task 3: Frontend session runtime, history and transport boundaries

**Files:** Create focused modules under `frontend/src/features/agent/` for operation ownership/history reconciliation and transcript leaves; create `frontend/src/lib/http/request.ts` if useful; modify `pages/Agent.tsx`, `stores/agent.ts`, `lib/api.ts`, `lib/apiAuth.ts`, `hooks/useSSE.ts`, `hooks/useAlphaJob.ts` only for ticket ownership; add covering store/hook/page tests.

**Interfaces:** Consume unchanged session API/SSE contracts and any Task 2 resync control. Produce typed session/view/operation/attempt guards and pure history mapping/reconciliation; optional AbortSignal API parameters preserve old callers. Keep ApiError identity/status/code and existing exported api functions.

- [x] Step 1: Deferred-promise regressions: stale report, send success/failure, cancel, goal/create result cannot mutate B after A→B or A→B→A; old cancel cannot stop a later attempt in A. Older history arriving after SSE completion preserves one answer/activity and optimistic messages; IDs stay unique after cached navigation.
- [x] Step 2: Record baseline RED against real page/store behavior; use fake external fetch only, not mock-component existence assertions.
- [x] Step 3: Centralize operation token checks and read abort ownership outside page callbacks. Use Task 1 conditional cancel with known attempt ID; pending-send cancel waits for send ACK and targets its exact ID. Extract pure history transform/reconcile and keep a revision barrier. Render base transcript before bounded/deduplicated run enrichment; ignore/abort obsolete reads. Use independent local message IDs. Move high-frequency subscriptions to transcript/activity leaves where render-count evidence supports it.
- [x] Step 4: Share bounded fetch/body deadlines with ticket auth; disconnect aborts pending handshake, reconnect gets a new single-use ticket, no-key loopback remains synchronous. Regress hung headers/body, superseded auth, unmount, timer cleanup, transient retry and read signal propagation. Preserve mutation uncertainty and never auto-repeat sends.
- [x] Step 5: Run existing Agent page, streaming store, useSSE/useAlphaJob, api/apiAuth, activity timer, runtime identity/grounding tests and a production build. Signed commit and report measured render/chunk facts without speculative speed claims.

### Task 4: Bounded cancellable provider call execution

**Files:** Create `agent/src/providers/call_runner.py`; modify `agent/src/providers/chat.py`, `agent/src/agent/loop.py` compaction waiting and minimal active-run cancellation binding at the provider-call boundary only; modify `agent/src/api/runtime.py` only for the process-scoped abandoned-producer counter handoff; tests `agent/tests/test_provider_call_runner.py` plus existing chat/stream/DSML/thought-signature/provider replay and runtime-summary suites.

**Interfaces:** A ProviderCallRunner executes blocking stream producers/calls behind finite capacity, copied context, monotonic deadlines and cancellation. Keep ChatLLM.stream_chat/chat signatures and LLMResponse unchanged; preserve ProviderStreamError mapping and encrypted/provider metadata.

- [x] Step 1: Fake provider blocks before first chunk, between chunks or in empty-stream invoke; cancel/deadline returns promptly with existing cancellation/error semantics, no later callbacks. Saturate producer capacity and reject without growing threads; capacity frees only after producer truly exits; contextvars survive. Workers are released/joined in test cleanup.
- [x] Step 2: Record baseline RED with bounded waits, Events and injectable capacity/clock; avoid permanently leaked test workers.
- [x] Step 3: Implement reusable call runner, queue-only producer, caller-owned callbacks and late-result fence. Integrate stream, no-chunks fallback and compaction wait with one process-scoped default producer budget (capacity 8) retained across runner/lifespan instances; permit releases only on producer exit, private injectable budgets for tests. Never synchronously close a generator while it is blocked in another thread; writes/tools are outside this module.
- [x] Step 4: Run new runner and existing stream retry, provider close, DSML, thought-signature, no-prefill, encrypted reasoning and compaction suites. Include timeout disabled plus cancellation and tiny-chunk continuity.
- [x] Step 5: Signed commit and report limits/cleanup guarantees for Tasks 5–6.

### Task 5: Tool execution, admission and write-safe replay

**Files:** Create `agent/src/agent/tool_execution.py` and `tool_replay.py` as appropriate; modify `agent/src/agent/loop.py`, `tool_progress.py` only when needed; modify `agent/src/api/runtime.py` only for the process-scoped abandoned-reader counter handoff; tests `agent/tests/test_tool_execution_lifecycle.py`, `test_tool_replay_write_barriers.py` and existing timeout/replay/cache/grounding and runtime-summary suites.

**Interfaces:** Produce typed prepared calls/results with index/call_id/raw_result/elapsed/executed/outcome, bounded ToolInvoker/ToolBatchExecutor and a dispatch-time replay policy. Keep original parameter cache identity, normalization and existing helper wrappers. Raw result enters grounding before redaction/truncation.

- [x] Step 1: Regress fast result progress before slow first call completes; more than eight tools never over-admit; cancel prevents queued tool invocation; timeout=0 read wait still cancels; active write finishes and subsequent tools do not launch. Ordered final transcript/trace keep one record per call_id; executed raw grounding remains ordered and cached/replayed results reuse prior canonical evidence without inventing new external observations.
- [x] Step 2: Regress `[write, replay-ready read]` reads fresh after write; `[read, write]` stays ordered; partial-effect error/exception invalidates mutable replay; denied/never-started writes do not invalidate; deterministic cache and no_cache/repeatable/recovery cap/authorization remain intact. Record baseline RED.
- [x] Step 3: Extract invoker and batch coordinator with bounded capacity/late fencing and copied context; process-scoped readonly-tool capacity 32 persists across AgentLoop/lifespan instances, permits cover actual worker exit. Observe completions promptly but keep final commit order. Batch capacity eight counts actual still-live producers, including timed-out/revoked ones, until their true exit; later calls receive explicit capacity outcomes when all slots remain held. Decide replay at actual dispatch after mutation barriers, conservatively invalidate for executed writes and retain pure deterministic caches.
- [x] Step 4: Run timeout, replay-after-compaction, deterministic cache, progress/no-progress/watchdog and grounding/identity/release covering suites. Review that worker threads never directly modify messages, evidence or trace.
- [x] Step 5: Signed commit and report interfaces used by Task 6.

### Task 6: One run terminal owner and accurate request budgets

**Files:** Create `agent/src/agent/run_lifecycle.py`; modify `agent/src/agent/loop.py`, `context_budget.py`, `agent/src/core/state.py` and `run_outcome.py` only if needed; tests `agent/tests/test_run_lifecycle_races.py`, `test_context_budget.py`, terminal/watchdog/state tests.

**Interfaces:** RunLifecycle owns generation, cancellation/done, true monotonic progress, requested stop reason and one terminal claim. Watchdog requests failure through this owner; main flow persists outcome once. ContextMeter binds observations to actual tools/model request shape while retaining compatibility for existing callers.

- [ ] Step 1: Barrier-controlled watchdog/finish races produce one end event and matching result/state; state write/trace write/close/initialization failures still set done and stop watchdog; reuse rejects concurrent run or fences old callbacks. Record baseline RED.
- [ ] Step 2: Regress tools→text-only→tools and model-shape changes against hand-derived token counts; missing/malformed usage does not corrupt estimate; static prompt/schema above window produces actionable failure rather than impossible compaction target.
- [ ] Step 3: Centralize terminal transition and outer finally cleanup: watchdog requests a stop, only main flow commits terminal once; non-returning active write remains in progress/stopping with retained ownership, not falsely terminal. Make RunStateStore atomic replacement. Integrate actual request-shape anchoring without changing prompt text, schema, model choice or grounding policy.
- [ ] Step 4: Run all Agent loop, state/outcome, context budget/compaction, tool evidence and provider integration covering suites. Verify no unsafe write retry or fake worker termination.
- [ ] Step 5: Signed commit and report; prepare final integration diff.

### Task 7: Whole-branch review and regression delivery

**Files:** Root updates README.md, design/plan status, ADRs under `docs/decisions/`, final verification report under `docs/architecture/`; reviewers consume complete branch diff and ledger. Change production only through one scoped fix dispatch for actual findings.

**Interfaces:** Consume all task reports and commits. Deliver verified architecture branch, compatibility notes, actual tests and explicit limitations.

- [ ] Step 1: Dispatch an independent whole-branch reviewer with diff/spec/ledger, covering cross-layer cancellation, persisted terminal identity, SSE ordering and resource ownership.
- [ ] Step 2: Fix actionable findings in one consolidated worker dispatch, then one scoped re-review; record decisions instead of silently dropping defects.
- [ ] Step 3: Root runs `.venv/bin/python -m pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q`, with loopback permission for local fixture servers; no live E2E. Run `tools/test_ci_env_var_gate.py`, `tools/ci_grep_gates.sh`, changed-file Ruff/syntax and `git diff --check`. Record all skips/failures.
- [ ] Step 4: Run frontend `npm run test:run` and `npm run build`, plus focused integration reruns only if final fixes justify them. Verify confidential code/history remains absent.
- [ ] Step 5: Update tracked ADR/design/README with final behavior, commits, regressions and limitations; pause the architecture heartbeat after all required work is complete. Keep branch local and unmerged for the user; no push while repository privacy is unresolved.
