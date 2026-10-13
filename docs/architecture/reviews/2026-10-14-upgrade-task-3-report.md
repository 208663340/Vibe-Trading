> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 3 — frontend session/history/transport implementation

Workspace and report directory: `/Users/will.he/Desktop/ai-metting/ai-repo/Vibe-Trading/.superpowers/sdd/2026-10-13-architecture-stability-upgrade/`.
Implementation base: `493a64bef93ee934f0078f0e3141dbd579160c92`. Bound spec: `/Users/will.he/Desktop/ai-metting/ai-repo/Vibe-Trading/docs/architecture/2026-10-13-stability-design.md`.

## Changes and exact interfaces

- New focused `frontend/src/features/agent/sessionRuntime.ts`: `OperationToken {sessionId, viewEpoch, operationId, kind, attemptId?}`; categories `send | cancel | history | goal | report | recovery`. `SessionRuntime.begin/current/commit/read/close` centralize ownership and read AbortControllers. `close()` invalidates the monotonically increasing store view epoch, aborts reads, and permits a subsequent StrictMode setup; it does not permanently dispose the ref or repeat a POST. The pending send registry independently retains each operation's cancel intent until ACK/rejection, including a backgrounded send.
- Store `beginOperation(kind, attemptId?)` and `commitOperation(token, updater): boolean` put guard and updater in one functional Zustand update. Independent category IDs prevent an unrelated history/goal read from superseding send. `switchSession/reset/invalidateView` revoke view tokens across A→B→A and unmount. Local IDs are `local:${crypto.randomUUID()}` and are never reset to integers. `confirmCachedSend` confirms the operation's durable user identity only in its session cache; `confirmedCacheMessages` promotes that evidence on the next owned cache/history read. `forgetSession` drops the deleted session cache.
- ACK promotes the optimistic user ID using operation identity, joins any already received durable SSE user record by message_id, and promotes the pending activity only if it still belongs to that send. A terminal SSE observation before ACK remains idle/terminal; old ACK/rejection cannot change a later view or attempt. Error messages retain requestText so Retry does not infer ownership from colliding IDs. `message.received` no longer deduplicates by text or timestamp proximity.
- `cancelPendingAttemptId: string | null` records accepted cancellation. Before ACK, cancel records intent and makes no cancel POST. After ACK the request carries that exact attempt ID. An accepted `{status: cancelled}` only sets cancelPendingAttemptId under the operation+attempt guard: status/activity/startedAt stay busy, repeated stop does not resubmit, and the composer/runPrompt cannot start a new send. SSE terminal or authoritative attempt snapshot releases it. `no_active_loop` initiates authoritative reads; it does not synthesize a stopped/completed outcome. Waiting-user snapshots release interaction without generating a done activity. Cancellation terminal preserves observed partial text and archives one activity.
- New `mapHistory.ts` is a pure DTO transform (including prompt, tool trail, runtime identity and original activity timing). `history.ts` exports `reconcileHistory(snapshot,current,startedRevision)`, stable identity helpers, `confirmUserMessage`, and `enrichRuns(ids,signal,load,concurrency=3)`. Durable users use message_id; linked assistant answers use `answer:${sessionId}:${attemptId}`; activity identity is the attempt within its guarded session domain; reports use session+attempt/message+run. Current entities newer than the captured transcript revision survive an older snapshot. Complete durable answers replace partial slots; equal text with separate user IDs remains separate. Base history commits before any report GET; unique run IDs have at most three enrichment workers, and aborted views stop queued work and abort active reads.
- `AgentStream.tsx` owns text, reasoning and activity/tool-progress subscriptions. The page retains status/layout/composer, messages and the primitive activity verb, so text/reasoning deltas no longer render the page. Existing chart/Markdown lazy loading remains in place; no new production dependency was added.
- `lib/http/request.ts` exports `boundedRequest<T>(url, init, read, timeoutMs)` and `RequestTimeout`. One deadline covers fetch headers, error decoding and body reads, races even an external fake that ignores abort, composes caller cancellation and clears timer/listener resources. `api.ts` maps timeout/network failures to the existing ApiError class/status/code/localized mutation-uncertainty message; writes are never replayed. `withAuthTicket(url, signal?)` uses this layer with a 15s total deadline. No-key useSSE still creates EventSource synchronously.
- Public API exports/facade and old callers remain compatible. Added optional signal parameters: `getRun(id,params={},signal?)`, `getSessionMessages(sid,signal?)`, `getGoal(sid,signal?)`, alpha stream URL helpers. Added `getSession(sid,signal?)` and `getAttempt(sid,attemptId,signal?) -> AttemptSnapshot {attempt_id,status,started_at,terminal_metadata}`. `cancelSession(sid,expectedAttemptId?)` preserves the legacy bodyless call and emits `{expected_attempt_id}` for upgraded callers.
- useSSE connection generations own/abort the ticket handshake on replacement, disconnect and hook unmount. `connect(url,handlers,cursor?)` adds an optional replacement cursor; reconnect mints a fresh ticket and retains Last-Event-ID query behavior. `session.resync` fences ordinary events; terminal/heartbeat/clear remain observable. A damaged EOF waits for hydration instead of opening another damaged stream. Page recovery reads messages+session concurrently, then the exact attempt snapshot, merges newer observed entities, and replaces the connection at latest_event_id. Failure stays recoverable/error with read-only Retry; neither transport open nor heartbeat proves synchronized history. `session_cleared` retains deletion/reset semantics. useAlphaJob changes only ticket AbortController ownership; original request_id submission recovery is unchanged.

## TDD and verification evidence

All fixtures use external fetch/EventSource doubles and the real Agent page, store, hooks, composer and transcript components. The page render measurement uses a call-through spy, not a replacement component. No credentials, live network, broker account or orders were used.

Initial RED commands (before transport/store implementation):

```sh
cd /Users/will.he/Desktop/ai-metting/ai-repo/Vibe-Trading/frontend
npm run test:run -- src/pages/__tests__/AgentSessionOwnership.test.tsx src/stores/__tests__/agent.test.ts src/lib/__tests__/apiAuth.test.ts
```

The added cache-ID regression failed (unique IDs 1 rather than 2); ticket headers/body waits reached Vitest's test deadline. Early page fixtures initially returned invalid goal/settings/live DTOs and were corrected; those fixture failures are not claimed as regression evidence.

After validating the real-page fixtures, the exact sanitized base production files were temporarily loaded with `git show 493a64bef93ee934f0078f0e3141dbd579160c92:<path>` for Agent/store/api/apiAuth/useSSE/useAlphaJob, and restored in Python `finally` from byte-for-byte current backups. No earlier confidential history was accessed. The valid baseline command was:

```sh
npm run test:run -- src/pages/__tests__/AgentSessionOwnership.test.tsx src/stores/__tests__/agent.test.ts src/lib/__tests__/apiAuth.test.ts src/hooks/__tests__/useSSE.test.ts
```

Output: **20 failed / 68 passed, four files failed, exit 1** (`/tmp/task3-final-red.log`). Failures include old history erasing the terminal answer; stale send rejection after actual separated A→B→A navigation; unscoped cancel before ACK; ACK not adopting user ID; stale report; old cancel releasing a newer attempt; history blocked by reports; unmount callback mutation under StrictMode; absent resync/recovery; late session/goal creation; accepted cancel exposing no busy wait; identical-user text dedup; ID collision; hung ticket headers/body; absent handshake abort. The measured baseline page rendered **12** additional times for 12 independent text+reasoning store updates.

Final covering command:

```sh
npm run test:run -- src/pages/__tests__/AgentSessionOwnership.test.tsx src/pages/__tests__/AgentBackgroundStreamCompletion.test.tsx src/pages/__tests__/AgentActivityTimerPersistence.test.tsx src/pages/__tests__/AgentGroundingStatus.test.tsx src/pages/__tests__/AgentRuntimeIdentity.test.tsx src/stores/__tests__/agent.test.ts src/stores/__tests__/streaming_area.test.tsx src/stores/__tests__/streaming_dom.test.ts src/hooks/__tests__/useSSE.test.ts src/hooks/__tests__/useAlphaJob.test.tsx src/lib/__tests__/api.test.ts src/lib/__tests__/apiAuth.test.ts src/components/chat/__tests__/ThinkingTimeline.test.tsx src/components/chat/__tests__/ConversationTimeline.test.tsx src/components/chat/__tests__/RunCompleteCard.test.tsx src/components/chat/__tests__/MessageBubble.test.tsx src/components/chat/__tests__/MessageBubble.math.test.tsx src/pages/__tests__/agentToolTimeline.test.ts
```

Output: **18 files / 178 tests passed**, exit 0 (`/tmp/task3-final-green.log`). Subsequent focused self-review fix verification:

```sh
npm run test:run -- src/pages/__tests__/AgentSessionOwnership.test.tsx
```

Output: **1 file / 20 tests passed**, exit 0 (`/tmp/task3-goal-fix.log`). This adds the goal.created-before-create-ACK ordering case: a newer read may own the goal snapshot, but it cannot suppress the send operation's exactly-once kickoff. The earlier covering suite contained 19 of these page cases; the final total is 179 focused tests when combined. The final supplemental fixture normalization result is recorded below.

```sh
npm run build
git diff --check
```

Both passed (`/tmp/task3-build.log`). Production output retained separate Agent, EnhancedMarkdown and chart chunks. Agent approximately **190.5 kB / 51.9 kB gzip**, EnhancedMarkdown **264.21 kB / 79.49 kB gzip**, vendor-charts **742.71 kB / 248.75 kB gzip**. This is a measured output snapshot, not a speed or size improvement claim. Existing Vite `configLoader: native` / `__dirname` warning and >500kB chart chunk warning remain; no warning filter was added. No React test warnings or uncaught production exceptions occur in GREEN.

Measured React result: the real Agent component's call-through render count increased by **0** for the same 12 separate updates; the leaf DOM contained all 12 `word ` fragments and final `reason 11`, and store text equaled `word `.repeat(12). Events were not ignored to achieve the count. No wall-clock speedup, FPS or browser memory claim is made.

## Files, self-review, limitations and handoff

Own changed production: `frontend/src/pages/Agent.tsx`, `stores/agent.ts`, `lib/api.ts`, `lib/apiAuth.ts`, `hooks/useSSE.ts`, `hooks/useAlphaJob.ts`; new `features/agent/{sessionRuntime,history,mapHistory,activity,AgentStream}` and `lib/http/request.ts`. Own tests: new AgentSessionOwnership; changed agent, apiAuth, useSSE tests, plus the two existing Agent history/timer suites' expectations for propagated AbortSignal.

Self-review fixed the acceptance-vs-terminal cancel boundary, same-content user dedup, stale cached optimistic identity after a background ACK, waiting-user interaction, completed REST vs partial slots, and goal.created-before-ACK independent ownership. Existing Agent.tsx remains large and keeps the established event/layout facade; the extracted modules stay focused. REST reads have no server snapshot revision transaction; ordering protection covers locally observed entity revisions and conditional attempt identities, not unobserved cross-client event races. A read/ticket abort or mutation timeout never proves the backend worker stopped. Optional report failure retains its established link fallback. No new public sessionStorage schema or authentication persistence was introduced.

Complete 744-test frontend baseline and full backend regression belong to root Task 7 and were not repeated here. Changed-file Ruff is not applicable: this task changes only TypeScript/TSX. Independent review is still required and belongs to root, not this worker. Root-owned spec/plan docs remain unstaged and outside the Task 3 commit.

Signed commit: `0cfee39c3eba21a9e72355a7405626223038cc25` — `fix(frontend): guard session operations and reconcile stream recovery`.
DCO trailer: `Signed-off-by: WillHe-Polymaker <will.he@polymaker.com>`.

Final supplemental normalization check: `npm run test:run -- src/pages/__tests__/AgentSessionOwnership.test.tsx` passed **1 file / 20 tests**, exit 0 at 18:28:22; this normalized old goal fixture status fields, without changing production. Final production build passed, Agent **190.57 kB / 51.85 kB gzip**. `git diff --cached --check` and post-commit `git diff --check` passed. The sandbox's read-only .git required the authorized local git command to run through escalation; auto-review approved it. The commit contains only the 18 Task 3 frontend files. Root-owned spec and plan remain the only modified tracked files after commit. This report is intentionally left for the root's documentation integration commit.

## Fix round 1 — independent review I1 / I2 (2026-10-13)

Fix base: `688f55eb76210e93549444dc4761b3bc4eb9ec40`; reviewed production was `0cfee39c3eba21a9e72355a7405626223038cc25`, followed only by root documentation. Read the full `task-3-review.md` before edits. Both Important findings were reproduced against the real page before production edits.

**I1:** Added `SessionRuntime.fenceRead(kind)` to revoke/abort just the superseded read category. Resync invokes `fenceRead("history")` before its authoritative reads, without changing viewEpoch or send/goal/cancel operation IDs. A successful recovery also increments transcriptRevision atomically and stamps every mapped/recovered entity with that committed revision using `stampMessages`. An older snapshot merge must consequently preserve those entities even apart from the revoked history token. A real-page deferred initial-history → successful terminal resync → old empty history response test verifies answer/activity/idle outcome remain intact and the superseded read's signal is aborted. A runtime test confirms independent send, goal and exact-attempt cancel commits remain admitted after fencing history.

**I2:** Replaced history enrichment's concatenation/last-key-wins merge with `mergeEnrichment(current,incoming,startedRevision,committedRevision)`. Each run worker captures its start revision immediately before the GET. The guarded store commit increments transcriptRevision and admits incoming rows only if the existing entity was not updated after that start; admitted enrichment rows receive the fresh committed revision. Unrelated transcript entities are retained. Real-page deferred tests exercise an old history run GET returning stale metrics or rejecting after a same-identity SSE report has committed newer metrics, equity and shadow details; the complete newer report survives exactly once in both cases. The real page also verifies fresh initial enrichment still supplies metrics. A helper test verifies an already-existing bare fallback can be improved by enrichment that began after it, preventing a reversed-concatenation workaround from freezing placeholders forever.

Readability: conventionally formatted the focused history/runtime modules and the new history/recovery methods in Agent.tsx; named ownership, settled, observed-terminal, snapshot-terminal and revision decisions. No broad page formatting, API/SSE changes, dependency/config changes or warning suppression.

RED (before the production fix):

```sh
cd /Users/will.he/Desktop/ai-metting/ai-repo/Vibe-Trading/frontend
npm run test:run -- src/pages/__tests__/AgentSessionOwnership.test.tsx
```

Result: **3 failed / 21 passed**, exit 1 (`/tmp/task3-fix1-red.log`, 19:22:08). I1 expected `["authoritative recovered answer"]`, received `[]`. I2 stale response changed total_return **0.5→0.1**, equity **150→110**, and dropped `shadow_fresh`; failure fallback dropped metrics/equity/shadow entirely. An initial test fixture omitted the established tool_result `status: ok`; it was corrected before this recorded RED so the failures exercise the overwrite paths rather than invalid shadow-tool input. Deferred resources are released in `finally`.

GREEN covering:

```sh
npm run test:run -- src/pages/__tests__/AgentSessionOwnership.test.tsx src/pages/__tests__/AgentBackgroundStreamCompletion.test.tsx src/stores/__tests__/agent.test.ts src/features/agent/__tests__/history.test.ts src/features/agent/__tests__/sessionRuntime.test.ts
```

Result: **5 files / 67 tests passed**, exit 0 (`/tmp/task3-fix1-covering.log`, 19:35:59). Includes the 24 real-page ownership/recovery cases, existing background completion and store coverage, and 5 focused helper/runtime cases. New helpers exercise real functions/state without replacing components. API/auth/SSE production code did not change; their earlier 178 covering run was not repeated.

```sh
npm run build
git diff --check
```

Both passed, exit 0 (`/tmp/task3-fix1-build.log`); Vite's existing future-native-loader/__dirname and >500kB vendor-chart warnings remain visible. No complete frontend/backend run was repeated; integration still belongs to root Task 7.

Self-review: recovery revokes one category and keeps mutation uncertainty/ownership; both recovery and admitted enrichment stamp monotonic entity revisions inside guarded commits; later report observations retain all fields; fresh enrichment remains functional. The pre-existing lack of a server-wide atomic snapshot revision remains unchanged. Independent scoped re-review is still owned by root.

Own files in this fix: `frontend/src/features/agent/history.ts`, `sessionRuntime.ts`, new `features/agent/__tests__/{history,sessionRuntime}.test.ts`, `pages/Agent.tsx`, `pages/__tests__/AgentSessionOwnership.test.tsx`. Root-owned docs are excluded from the local signed fix commit. Signed fix commit SHA is appended after creation below.

Fix 1 signed commit: `7579d540f8c38877ba439b43e6d42a785cfb028a` — `fix(frontend): preserve newer recovery and enriched report entities`.
DCO: `Signed-off-by: WillHe-Polymaker <will.he@polymaker.com>`. Post-commit `git diff --check` passed; only root-owned plan remains modified.
