> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 7 consolidated final fix report

Status: DONE. Scope: whole-branch I1/P1 and I2/P2 (mandatory O1).
Base: `8a745c4072a5cec58b3366fc72baa43b7c8d79d6` on
`codex/architecture-stability-upgrade`. Signed commit:
`3fab9117dce6f5b0631fc2ffdb7d1c69743a45b2`.

## Interfaces and implementation

- `frontend/src/hooks/useSSE.ts`: `useSSE(config?)` and its public
  `connect(url, handlers, cursor?)`, `disconnect`, status access/subscription remain
  unchanged. After existing source/generation and damaged-stream fences,
  `session.resync` and `session_cleared` bypass ordinary last-event-ID cursor mutation
  and LRU dedup. Native EventSource can inherit the last wire ID for ID-less controls;
  that ID does not prove ordinary-stream continuity. The existing recovery handler
  still owns authoritative hydration and explicit-cursor replacement. Damaged EOF
  remains waiting for hydration; accepted indexed terminal events are still delivered.
  Ordinary indexed event dedup, reconnect resume and fresh single-use ticket paths are
  unchanged.
- `frontend/src/pages/Agent.tsx`: completion's optional `api.getRun(runId, {}, signal)`
  captures `act().transcriptRevision` before starting the GET. Both success and failed
  GET fallback feed the existing functional `runtime.commit(reportToken, ...)` and
  `mergeEnrichment(current, incoming, startedRevision, committedRevision)` instead of
  constructing an unguarded snapshot that can overwrite a newer entity. Report ID
  remains `report:${sid}:${attemptId}:${runId}`. Newer report metrics, equity, shadow
  and metadata are retained as one entity; fresh reads/fallbacks still add the card.
  Existing abort, operation, session/view and attempt fences remain intact.
- No public DTO, facade, event name/payload, helper signature, dependency, POST replay,
  grounding rule, backend, or unrelated architecture changed.

Applied the user-invoked Vercel React best practices skill at
`/Users/will.he/.agents/skills/vercel-react-best-practices/SKILL.md`; relevant rules
were functional state updates and event-handler refs. No new subscription or derived
state was introduced.

## Test changes and coverage

- `frontend/src/hooks/__tests__/useSSE.test.ts`: fake EventSource preserves its wire
  last-ID buffer across frames without an explicit ID. Real-hook deferred hydration
  regression starts with indexed text plus ordinary replay, then inherited-ID resync,
  damaged delta, retained indexed terminal, and damaged EOF. It verifies no premature
  reconnect, hydration replacement at payload `event-9`, recovered ordinary dedup,
  and subsequent reconnect resume at indexed `event-10`. Other regressions cover
  inherited-ID clear, controls not acknowledging/deduplicating an unseen inherited ID,
  and saved old-source callbacks rejected after generation replacement.
- `frontend/src/pages/__tests__/AgentSessionOwnership.test.tsx`: page transport fake
  also preserves wire IDs. Existing real-page recovery regression now begins with an
  indexed attempt event before ID-less resync. Added real-page indexed-terminal →
  inherited-ID clear verifies state/cache removal and route reset.
- Deferred real-page old SSE report GET → later history report enrichment → old stale
  success and old failure regressions verify exactly one fresh row and terminal
  answer, fresh metrics 0.5/equity 150, and full optional-field preservation. The
  optional `shadow_fresh` field is committed through the real store boundary after
  the actual history enrichment, since that history mapper does not create shadow
  IDs. Fresh SSE success (metrics 0.3/equity 130) and fresh error fallback remain
  positive controls. Existing reverse-order tests and helper fallback improvement
  tests run in the covering suite.
- Deferred work is released in `finally`, pages/hooks unmount, timers/connections are
  cleaned up. Only fetch and EventSource external boundaries are faked; hook, page,
  API client, runtime guards, history merge and store are real.

## RED evidence

Working directory for npm commands: repository `frontend/`.

```bash
npm run test:run -- src/hooks/__tests__/useSSE.test.ts src/pages/__tests__/AgentSessionOwnership.test.tsx > /private/tmp/task-7-fix-red.log 2>&1
```

Run before production edits, exit **1**, **2 failed files / 6 failed + 47 passed**.
Expected failures:

1. Real hook resync handler called 0 times instead of once after inherited ID.
2. Real hook clear handler called 0 times instead of once after inherited ID.
3. Real page authoritative gap recovery created 1 EventSource instead of 2.
4. Old SSE stale success replaced the fresh history report metrics/equity/shadow.
5. Old SSE failure replaced those fields with an empty fallback report.
6. Real page inherited-ID clear retained session `A` instead of clearing it.

After first GREEN, strengthened the control-cursor regression to include an indexed
wire event with no subscribed listener. Temporarily restored only the original hook
dedup block, ran the targeted test, then restored the fixed block before covering:

```bash
npm run test:run -- src/hooks/__tests__/useSSE.test.ts -t 'does not treat an ID-less clear' > /private/tmp/task-7-fix-cursor-red.log 2>&1
```

Exit **1**, **1 failed / 23 skipped** (name filter). Expected failure:
`Expected /events?Last-Event-ID=event-1; Received /events?Last-Event-ID=event-9`.
The ID-less control wrongly acknowledged the unseen inherited ID. Fixed GREEN also
verifies that subsequent indexed event-9 remains deliverable and replay is deduped.
No commits or HEAD changes occurred during this controlled original-block probe.

## GREEN, build and static verification

Initial two-file GREEN used the same RED command with output
`/private/tmp/task-7-fix-green.log`: exit **0**, **2 files / 53 tests passed**.

Final covering command after strengthening the cursor regression:

```bash
npm run test:run -- src/hooks/__tests__/useSSE.test.ts src/pages/__tests__/AgentSessionOwnership.test.tsx src/pages/__tests__/AgentBackgroundStreamCompletion.test.tsx src/pages/__tests__/AgentActivityTimerPersistence.test.tsx src/pages/__tests__/AgentRuntimeIdentity.test.tsx src/pages/__tests__/AgentGroundingStatus.test.tsx src/pages/__tests__/agentToolTimeline.test.ts src/features/agent/__tests__/history.test.ts src/features/agent/__tests__/sessionRuntime.test.ts src/stores/__tests__/agent.test.ts src/stores/__tests__/streaming_area.test.tsx src/stores/__tests__/streaming_dom.test.ts src/lib/__tests__/api.test.ts src/lib/__tests__/apiAuth.test.ts src/lib/__tests__/runReports.test.ts > /private/tmp/task-7-fix-covering.log 2>&1
```

Exit **0**, **15 files / 171 tests passed**, duration 7.42s. Includes report/fallback
merge, both cross-read orderings, operation ownership, ABA/unmount, background
completion, timers, runtime/grounding identity, tool timeline, store, leaf streaming,
HTTP/auth ticket and report-worthiness compatibility.

```bash
npm run build > /private/tmp/task-7-fix-build.log 2>&1
git diff --check
git diff -- frontend/src/hooks/useSSE.ts frontend/src/pages/Agent.tsx
git diff -- frontend/src/hooks/__tests__/useSSE.test.ts frontend/src/pages/__tests__/AgentSessionOwnership.test.tsx
git diff --cached --stat
git rev-parse HEAD
```

Build exit **0**: `tsc -b && vite build`; Vite transformed **3203 modules**, built
production output successfully. `git diff --check` exit **0**, empty output. Read
complete production and test diffs for self-review. Index was empty before scoped
staging, HEAD was the authorized base. No Python changed, so changed-file Ruff is
not applicable. TypeScript compilation is included in the build.

Warnings are retained: tests print existing Vite native-config `__dirname` warning;
build prints existing >500kB chunk warning. No warning filters or suppressions used.

## Self-review, limits and handoff

- Reviewed all four changed frontend files. Source ownership validation precedes
  every control effect; damaged ordinary data stays fenced; controls cannot update
  cursor/dedup. No missed-stream completion claim is added.
- Both report resolution paths share the same start revision and guarded merge.
  Existing operation/view guard remains outside and inside commit. No stale closure,
  manual identity bridge, unrelated message deletion or duplicate report introduced.
- Scoped to the two assigned production boundaries and coherent tests. Existing
  dense unrelated page code was not reformatted or rewritten. No new dependency.
- Native-browser/network/provider/account/live integration was not run. Tests model
  native SSE wire ID inheritance, not a claimed live-browser run. Optional shadow
  preservation is explicitly injected at the store boundary as described above.
- Full frontend/backend, repository six gates and final independent scoped re-review
  belong to root. No full suites, subagents, remote/source fetch/push, confidential
  object recovery, credential work or sensitive Git metadata cleanup performed here.
- Report is task evidence; only the four frontend implementation/test files are in
  the signed local fix commit. Controller-owned docs are never staged by this worker.

## Signed local commit

```bash
git add frontend/src/hooks/useSSE.ts frontend/src/hooks/__tests__/useSSE.test.ts frontend/src/pages/Agent.tsx frontend/src/pages/__tests__/AgentSessionOwnership.test.tsx
git diff --cached --check
git diff --cached --stat
git diff --cached --name-only
git commit -s -m "fix(frontend): guard SSE controls and stale report reads"
git log -1 --format=full
git show --stat --oneline HEAD
git status --short
git diff --check
git diff --cached --check
```

Initial sandboxed `git add` was denied by filesystem protection of `.git/index.lock`
(exit 128), not an automatic-review rejection. The same exact scoped add and signed
commit succeeded through the approved Git metadata escalation; no broader staging.

Commit **`3fab9117dce6f5b0631fc2ffdb7d1c69743a45b2`**, subject
**`fix(frontend): guard SSE controls and stale report reads`**. Verified actual
`Signed-off-by` trailer. Commit contains exactly **4 files / 219 insertions /
13 deletions**. Post-commit `git status --short`, working/index diff checks were empty
and exit 0. The task evidence report remains outside the commit for root delivery.
