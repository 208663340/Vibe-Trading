> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 3 review — 493a64be..0cfee39c

## Spec Compliance

- ❌ Issues found: the per-entity ordering barrier is incomplete for REST recovery and report enrichment. A late history read can erase a newer authoritative recovery, and late enrichment can replace a newer report with stale/fallback data (`frontend/src/pages/Agent.tsx:1174`, `frontend/src/pages/Agent.tsx:547`, `frontend/src/features/agent/history.ts:9`). See Important findings below.
- ✅ The reviewed changes implement category-scoped view ownership and atomic guarded commits (`frontend/src/features/agent/sessionRuntime.ts:4`, `frontend/src/stores/agent.ts:146`); exact-attempt pending cancellation and ACK promotion (`frontend/src/pages/Agent.tsx:1340`, `frontend/src/pages/Agent.tsx:1355`); bounded, deduplicated base-history enrichment (`frontend/src/features/agent/history.ts:18`); total HTTP deadlines and composed cancellation (`frontend/src/lib/http/request.ts:5`); and leaf streaming subscriptions (`frontend/src/features/agent/AgentStream.tsx:8`).
- ⚠️ Cannot verify from this task diff: server-side cancellation admission/actual worker exit, durable attempt snapshot authority, SSE server cursor coalescing and terminal retention, and the complete cross-task regression gate. These belong to Tasks 1, 2 and 7; their supplied contracts were used as requirements, not re-reviewed.

## Strengths

- `frontend/src/features/agent/sessionRuntime.ts:11` gives operation/read ownership a focused interface; `frontend/src/stores/agent.ts:146` performs ownership validation and update in the same functional store operation. The runtime remains reusable after StrictMode cleanup rather than permanently disposing the ref.
- `frontend/src/pages/Agent.tsx:1340` retains activity/busy state after accepted cancellation, while terminal handling releases it. The page tests exercise the actual composer, pending ACK cancellation, terminal-before-ACK, A→B→A, unmount, background cache confirmation and waiting-user recovery (`frontend/src/pages/__tests__/AgentSessionOwnership.test.tsx:42`).
- `frontend/src/lib/http/request.ts:5` covers both headers and body with one deadline, including fetch doubles that ignore abort, and removes timer/listener resources. Existing API error identity and optional-argument compatibility are retained by the facade (`frontend/src/lib/api.ts:17`). Alpha changes stay limited to ticket ownership (`frontend/src/hooks/useAlphaJob.ts:21`).
- `frontend/src/pages/Agent.tsx:1164` fetches independent session/messages data concurrently, consistent with Vercel `async-parallel`. `frontend/src/features/agent/AgentStream.tsx:10` and the primitive page activity-verb subscription follow the relevant subscription rules. The call-through page test checks both zero parent renders and delivered text/reasoning DOM (`frontend/src/pages/__tests__/AgentSessionOwnership.test.tsx:94`); it does not obtain the count by dropping events.
- `frontend/src/features/agent/mapHistory.ts:6` bridges linked attempt identity, while `frontend/src/features/agent/history.ts:25` promotes optimistic user identity without deduplicating intentional repeated content. The new focused modules have substantive responsibilities; the pre-existing facade size alone is not a finding.

## Issues

### Critical (Must Fix)

- None found.

### Important (Should Fix)

1. **A history request started before resync can erase the successful recovery.** `frontend/src/pages/Agent.tsx:1174` writes mapped recovered rows without assigning a fresh entity revision or advancing `transcriptRevision`; starting recovery does not invalidate/abort the independent history operation. At `frontend/src/features/agent/history.ts:13`, a current durable row without a revision is discarded when it is absent from the incoming snapshot. Concrete order: initial history GET captures an empty/older transcript; `session.resync` independently loads the completed reply and commits it; initial GET finally returns its older snapshot, its history token is still valid, and it removes the recovered answer/activity. The UI can now be idle with its authoritative terminal answer gone, violating the snapshot/live ordering requirement. Fence superseded history reads when recovery starts and/or stamp recovered entities with a monotonic revision that the older read must preserve. Add the real-page deferred initial-history → successful resync → old-history-resolution regression.

2. **Older report enrichment overwrites a newer same-identity report.** `frontend/src/pages/Agent.tsx:547` constructs a snapshot as current messages followed by old `mapped.pendingRuns` results and calls `reconcileHistory(..., [], ...)`. `frontend/src/features/agent/history.ts:9` keeps the last duplicate key, so the old entry wins unconditionally; no captured revision comparison occurs. Concrete order: an initial history row starts a slow run GET; a terminal SSE event independently reads and commits the newer report with metrics/equity/shadow details; the initial GET later fails or returns stale data and replaces that report with a bare fallback/stale row. Independent report/history tokens intentionally do not invalidate one another, so the ownership guard does not prevent this. Use an entity-aware enrichment merge that retains rows updated after the enrichment began, and add a deferred old-enrichment-after-new-report regression.

### Minor (Nice to Have)

- `frontend/src/features/agent/sessionRuntime.ts:14`, `frontend/src/features/agent/history.ts:25`, and `frontend/src/pages/Agent.tsx:1169`: several new state-machine methods and recovery branches are packed into long single lines. Their responsibilities are useful, but conventional formatting and named intermediate decisions would make the ownership/revision rules substantially easier to audit. This is a new-code readability concern, not an objection to the old page size.
- Existing validation noise remains visible: `/tmp/task3-final-green.log:6` and `/tmp/task3-goal-fix.log:6` report Vite's `__dirname`/future native-loader warning; `/tmp/task3-build.log` reports the existing >500 kB vendor-chart chunk. These are acknowledged pre-existing warnings, not new Task 3 failures or blockers; no suppression was introduced.

## Checks and evidence

- Read the supplied task brief, handoff, report, bound design and Vercel React skill/rules. Read the 2,694-line review diff in non-overlapping chunks; recovered only the output-truncated Alpha/SSE segment. No git commands or suite reruns.
- Changed-file exception: inspected only `Agent.tsx:805` completion-handler continuation and `Agent.tsx:1180`–`1330` setup/session-switch/cleanup continuation because the diff cuts those functions. Named risk: determine whether same-view SSE replacement invalidates the outstanding history operation and whether StrictMode cleanup reconnects. It does reconnect, but does not revoke history on recovery. No broader code crawl.
- Focused, read-only Node 26 execution imported the actual `features/agent/history.ts` (no production/test edits): recovered durable answer → older empty history produced `afterRecovery:["authoritative terminal"]`, then `afterOlderHistory:[]`. Current report with revision 2 and metrics → the actual enrichment-shaped merge with an old same-key fallback produced a report without metrics. These are the two doubts not answered by the existing run, and only these pure-function probes were executed.
- Inspected existing log summaries: baseline 20 failed / 68 passed; covering run 18 files / 178 passed; final supplemental page run 20 passed (20 versus previous 19 page cases yields reported 179 combined); production build succeeded with Agent 190.57 kB / 51.85 kB gzip. No claim of running the entire suite again.

## Assessment

**Task quality: Needs fixes.**

**Reasoning:** Operation ownership, transport deadlines and subscription separation are meaningful improvements with strong behavioral coverage. The two reproducible older-REST overwrite paths still violate the central reconciliation guarantee, so Task 3 needs those narrow fixes and regressions before approval.
