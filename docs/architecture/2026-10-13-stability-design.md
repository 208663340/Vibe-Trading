# Architecture and stability upgrade

Date: 2026-10-13. Baseline: `c25fea2bc261a2d0ed971c989d194de9f04ec963`.
Branch: `codex/architecture-stability-upgrade`.

## Purpose and acceptance criteria

The user requested a multi-agent plan and implementation to make the existing frontend,
backend and Agent kernel dependable, followed by regression testing. This is an
incremental architecture migration with observable behavior changes, not a framework
replacement or a file-size exercise. The three evidence reports under `reviews/` are
the design inputs. Each defect must have a regression that failed on the baseline.

1. A session has one execution owner from admission until its actual worker exits.
   Immediate cancellation cannot strand it; deleting an active session returns 409;
   shutdown stops admission and cannot allow an old writer to race recovery.
2. Durable terminal results are authoritative. Search/index/notification failure cannot
   turn a completed response into a failed attempt; deleted sessions disappear from
   search, and partial persistence failures retain recoverable evidence.
3. SSE subscribers own their loops and resources. Closing a full subscriber queue must
   terminate it, and terminal information must not silently disappear behind deltas.
4. Frontend operations own a session, a view generation and an operation/attempt.
   Delayed send, cancel, completion, goal and history results cannot mutate another
   session or a later attempt. History reconciliation retains newer stream answers
   exactly once; message IDs stay unique across the five-session cache.
5. Base history renders before report enrichment, which is deduplicated and bounded.
   Ticket fetch and body read have deadlines and caller cancellation; disconnect owns
   all transport resources. High-frequency transcript subscriptions belong to leaves.
6. Provider waits honor cancellation and idle deadlines even when `next()` or fallback
   `invoke()` blocks. Producer capacity is bounded; abandoned workers keep their permit
   until actual exit. Context variables and provider metadata survive the boundary.
7. Tool batches have bounded admission, do not launch queued work after cancellation,
   observe fast completion immediately, and commit model/grounding results in call
   order exactly once. Running write tools are awaited rather than forcibly killed.
8. Replay is evaluated at dispatch after preceding writes. Every executed write
   conservatively invalidates mutable read replay, including partial failure; blocked
   or never-started calls do not pretend to have mutated state.
9. A run has one terminal transition and unconditional resource cleanup. State JSON is
   replaced atomically; watchdog and caller cannot publish conflicting terminal states.
   Context accounting binds usage to the actual message/tool/model request shape.
10. All implementation tasks pass independent review, focused regressions, the complete
    non-live backend suite, complete frontend suite, production build and repository
    gates. Environment omissions or pre-existing failures are reported by name.

## Compatibility and constraints

- Python >=3.11; existing React 19/Vite/TypeScript/Zustand frontend. No new production
  dependency or database is required. Keep existing LangChain provider adapters and
  custom ReAct entry points; do not introduce LangGraph or microservices.
- Preserve public `AgentLoop.run/cancel`, `ChatLLM` return shapes, tool JSON schemas,
  function names imported by tests and the frontend `api` facade. Preserve existing
  helper re-exports where consumers rely on them.
- Preserve API paths, success response shapes, `detail` string errors and existing SSE
  names/payload fields, including replay cursor and single-use tickets. New busy-delete
  errors use 409; runtime closing/admission unavailable errors use 503.
- Filesystem JSON/JSONL and SQLite FTS remain the stores. SQLite is a rebuildable
  projection; transcript-first terminal persistence and interrupted-attempt recovery
  remain the crash consistency mechanism. This does not promise multi-process writers.
- Authentication, broker mandates, kill switch, audit and grounding checks remain
  enforced. No credentials, live accounts, real orders, publishing, push or deployment.
- Do not restore previously removed confidential strategy code, historical objects or
  cached copies. Work begins on the sanitized baseline, not on the old remote branch.
- Read cancellation may abort its HTTP request; mutation abort never proves a server
  operation stopped. Do not replay session sends automatically or retry uncertain
  writes. Preserve existing alpha-job recovery keyed by request_id and fresh ticket
  minting on SSE reconnect; these explicit idempotent paths are not removed.
- Python threads cannot be safely force-killed. Bounded admission, context propagation,
  cooperative cancellation and late-result fences are the guarantees.
- Keep reports/ADRs/plans tracked explicitly; other locally ignored docs remain ignored.
- Run expensive complete regression once at the integration gate, not concurrently in
  every worker. Workers run and record relevant focused suites; the controller owns the
  full integration command and regression environment.

## Backend contract and responsibility boundaries

Routes validate/authenticate and map domain exceptions. SessionService coordinates
session operations and durable outcomes. A separate execution supervisor owns claims,
tasks, underlying worker futures, cancellation and resource admission. RuntimeServices
owns the executor, service lifecycle and shutdown order at the API boundary.

| Operation | Existing success contract | Explicit failure boundaries |
|---|---|---|
| POST `/sessions` | Existing session model | 422 invalid input; 501 disabled; 503 closing |
| POST `/sessions/{id}/messages` | `{message_id, attempt_id}` | 404 absent; 409 busy; 503 admission unavailable |
| POST `/sessions/{id}/cancel` | Existing accepted/no-active status | Ack is acceptance, not proof of worker exit |
| DELETE `/sessions/{id}` | `{status: deleted, session_id}` | 404 absent; 409 active; failed I/O cannot return success |
| GET session/messages/events | Existing DTOs and event names | REST remains authoritative after reconnect |

The supervisor linearizes send versus delete under one gate, observes wrapper and
worker completion separately, and protects later tasks from earlier callbacks. Its
close result describes still-running work honestly. Terminal persistence is separate
from optional projections and keeps committed outcomes monotonic. Application shutdown
stops scheduler/channel admission, then session execution, then event/resources. Legacy
API-server monkeypatch/re-export seams stay at the boundary during migration.

Cancel accepts an additive optional JSON body `{expected_attempt_id: string}`. When
present, the comparison and cancellation admission occur under the same supervisor
gate; mismatch returns the existing no-active status and cannot cancel another attempt.
Legacy no-body callers keep their current behavior. The upgraded frontend sends the
known attempt ID, or holds a cancel intent during pending send and conditionally
cancels that exact ID after acknowledgment, rather than issuing an unscoped cancel.

An attempt claim transitions reserved/submitted/running/finalizing/released. Release
requires the actual main worker exited (or is confirmed never started) AND finalization
finished or reached a recorded recoverable persistence failure. A pre-start cancellation
is finalized by the supervisor's registered completion path, even if the coroutine
body never ran. The finalizer itself retains the claim; old callbacks compare handle
identity before modifying a later owner. CloseSummary includes complete,
pending_run_writers, pending_finalizers and abandoned_readers/producers; the last group
can outlive main completion only after callback/evidence fencing and retains its pool
permits. OPEN/CLOSING/CLOSED state is monotonic and repeated close is idempotent.

Durable finalization decision table: no transcript commit means execution/persistence
failure can still be recorded; transcript committed but attempt update failed means
leave the attempt recoverable and use the linked terminal reply on restart; attempt
terminal committed means checkpoint/index/notification failures cannot change it.
Reply identity and terminal commit are idempotent by attempt_id. Shutdown timeout does
not release a live main worker's ownership or permit same-store recovery. The API
runtime retains that closing owner until the worker exits, then permits reinitialization.

Terminal evidence is a complete JSONL assistant record linked to attempt_id with stable
message identity, metadata status (completed/cancelled/failed), timing and runtime facts;
even empty-content cancellation/failure has such a record. Existing message parsing must
not treat a partial final JSONL line as committed. Recovery adopts complete terminal
evidence exactly once; absent evidence retains the existing interrupted-attempt policy.
Guarantees cover process interruption and atomic file integrity, not a new multi-file
transaction or an unproven power-loss guarantee.

SSE overflow uses additive `session.resync` control carrying a reason and the latest
buffered cursor. A full queue drops/coalesces old data only to guarantee this control,
terminal and close delivery. Clients reload authoritative session/messages state on
resync without replaying a user POST; old stream cursor does not imply complete delta
delivery. Task 2 reports the exact payload to Task 3 before implementation.

Gap controls are per-subscriber and do not rely on free data queue slots. After overflow
or an expired replay cursor, the damaged stream signals resync and stops forwarding
ordinary deltas until replaced. Task 3 fences transient state, loads authoritative
messages/session attempt status, reconciles newer observations, then reconnects at the
new cursor. A transport open/heartbeat is not proof of synchronized history. Failed
hydration keeps recoverable/unknown state, never fake completion or a resent mutation.
Existing REST attempt metadata is the authority for cancelled/failed with empty text.

## Frontend responsibility boundaries

A session runtime/controller owns operation tokens and read cancellation. Pure history
mapping/reconciliation owns stable identities and snapshot/live ordering. A shared HTTP
layer owns bounded fetch/body reads and composed AbortSignal. SSE transport owns ticket,
connection generation, cursor, retry and cleanup. Zustand owns guarded state commits;
the page owns routing/composer/layout, and transcript/activity leaves select only the
state they render. Preserve background execution and cached drafts on navigation.

View epochs are monotonic across A→B→A and unmount/remount. Operation tokens are scoped
by category (send/cancel/history/goal/report), not one invalidation counter for all work.
A pending send has an operation ID before session creation and promotes once to real
session/message/attempt IDs; a late ACK cannot downgrade an already observed terminal
attempt to streaming. Token validation plus state commit occurs in one functional
store update. View navigation/toast/composer side effects and background session-cache
confirmation are separate ownership decisions.

History entity keys: durable user message_id (ACK maps the local optimistic ID);
assistant slot `(session_id, attempt_id, answer)` bridged from REST linked_attempt_id;
activity slot `(session_id, attempt_id, activity)`; report slot `(session_id, attempt_id,
run_id)`. Revisions are per relevant entity so old REST cannot erase newer stream
state, and durable IDs can replace provisional IDs without duplicate answers. Do not
deduplicate by message content: identical consecutive prompts can be intentional.

## Agent kernel responsibility boundaries

ProviderCallRunner owns bounded producer admission, cancel/deadline waiting, copied
context and abandonment fencing. ToolInvoker owns invocation progress/deadline/resource
leases; ToolBatchExecutor owns finite concurrent admission and ordered observation/
commit boundaries. Replay policy owns observation validity across writes. RunLifecycle
owns stop reasons, true progress, one terminal claim and unconditional cleanup. The
AgentLoop remains the orchestration facade, with grounding and raw result ingestion at
the single commit boundary before truncation/redaction.

Production provider and readonly-tool budgets are process-scoped defaults shared by
successive runner/AgentLoop/API lifespan instances (provider capacity 8, readonly-tool
capacity 32); constructors may inject a private budget for tests. A permit covers the
actual producer/tool thread, not the waiting wrapper, and is released only on true
exit. Runtime close fences callbacks; it does not recreate pools to evade occupied
slots. Detached readonly/provider producers never write transcript/evidence/state or
emit events after their lease is revoked. Running mutating tools remain under the
main attempt owner. Admission exhaustion is explicit, not an unbounded queued wait.

One logical provider attempt acquires one lease: fallback invoke runs in the same
producer or only after the prior producer exits; compaction has one runner layer, not
nested acquire. Request payloads are independent snapshots. Producer queue is bounded,
put polls lease revocation, terminal status has an independent slot, and permits cover
thread start/iterator cleanup failures too. Idle timing uses effective provider arrival,
not a slow caller callback; compaction retains its total wall-clock limit. Owned client
cleanup follows active leases and must not imply a blocked producer exited.

Watchdog requests a stop reason through RunLifecycle; only the main run flow persists
the terminal outcome once. If an already-executing write never returns, no false
terminal/worker-exited claim is emitted: the run remains in progress/stopping and API
shutdown reports unfinished ownership. No automatic uncertain-write retry is allowed.

Existing established stall reason wins over user cancellation before terminal claim;
requests after terminal claim cannot rewrite it. Pre-start cancel is finalized by the
session supervisor; once a main run worker started, SessionService uses its unique run
outcome, not a second outcome synthesized from wrapper CancelledError. Done describes
facade exit/cleanup, not the disappearance of detached fenced readers.

Tool completion observation only updates accepted true progress early; ordered final
commit includes executed, blocked, cancelled, compact, deterministic and replay results.
Workers never mutate evidence/messages/trace. Executed-write invalidation linearizes
after authorization/cancel admission and immediately before registry.execute, even
when execute or later persistence fails. Existing authorization-before-cache and
run-scoped replay visibility/recovery rules remain unchanged.

For request token accounting, snapshot message estimate, actual sent tools estimate,
configured model and request ID before sending. Same-model next estimate is
anchor_actual + (next_messages - anchor_messages) + (next_tools - anchor_tools).
Configured-model changes discard the old anchor; invalid/missing usage cannot replace
valid calibration. Response model aliases are recorded without needlessly resetting a
stable configured identity. Compaction summary usage never calibrates the main request.

## Validation and rollback

Use temporary filesystem/SQLite state, fake providers, controlled Events/barriers and
deferred browser promises. Never depend on a real model, real brokerage, long arbitrary
sleeps or source-text assertions. Each behavior change gets a red/green regression.
Existing prompt, identity, encrypted reasoning, DSML, tool-call, replay, cancellation and
frontend hydration suites form compatibility characterization coverage. Each task has
its own signed commit and independent review; revert an individual commit to roll back
its boundary while keeping public schemas stable.

Baseline evidence: frontend 77 files / 744 tests passed. Backend collection succeeded
with 17,937 tests. The first backend run stopped at 15 failures, 10,902 passed and 160
skipped: 13 failures were loopback bind restrictions and two lacked statsmodels. The
loopback rerun and CI-declared optional dependency setup are recorded in the progress
ledger; their outcomes are not assumed here.
