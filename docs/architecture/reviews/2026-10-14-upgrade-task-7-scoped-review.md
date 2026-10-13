> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 7 final scoped re-review

## Finding verdicts

- **I1 / P1 — ID-less recovery controls with inherited Last-Event-ID** — **ADDRESSED**. `frontend/src/hooks/useSSE.ts:101` retains the source/generation fence before effects; `:104` marks resync damage and retains terminal/clear delivery; `:108–112` classifies both `session.resync` and `session_cleared` before ordinary cursor mutation and LRU dedup. An inherited control ID therefore neither suppresses the handler nor acknowledges data continuity. Ordinary indexed events still update the resume cursor and deduplicate. `:139` leaves damaged EOF awaiting authoritative hydration, while `:187–207` resets damage/dedup and replaces the generation at the explicit hydration cursor. The existing real-page recovery handler at `frontend/src/pages/Agent.tsx:1193–1249` is now reachable after an indexed event and still owns reads and replacement.
- **I1 verification** — `frontend/src/hooks/__tests__/useSSE.test.ts:29` now preserves the wire last-ID buffer. Added regressions at `:57`, `:102`, `:113` and `:133` exercise indexed data → inherited-ID resync → suppressed damaged data → retained indexed terminal → damaged EOF without premature reconnect → deferred hydration replacement at payload event-9 → ordinary replay dedup and resume at event-10. The clear regression includes an unseen inherited event-9 and verifies resume stays at event-1 and later indexed event-9 remains deliverable. Captured old-source control callbacks remain fenced after replacement. The page transport fixture also preserves IDs, and the real-page recovery and clear tests cover replacement and removal of session/cache/route state. These are fake transport tests of the real hook/page, not live-browser verification.
- **I2 / P2 — Mandatory O1: stale SSE report GET overwrites newer history enrichment** — **ADDRESSED**. `frontend/src/pages/Agent.tsx:952` captures transcript revision before the awaited GET at `:957`. Success and catch fallback converge on the same guarded functional commit at `:968–978`, passing current messages, the durable report entity ID, the read-start revision and the commit revision to the existing `mergeEnrichment`. The previous unguarded snapshot append with empty current messages is removed. Existing operation ownership and read-abort checks remain at `:966`. The SSE path now uses the same guarded entity merge as the history enrichment path at `:576`.
- **I2 verification** — `frontend/src/pages/__tests__/AgentSessionOwnership.test.tsx:346` tests both old SSE GET → later real history enrichment → stale success and old failure. It verifies exactly one report row equal to the newer entity, fresh metrics 0.5/equity 150, preserved optional shadow field and one terminal answer. The shadow field is explicitly injected through the real store after actual history enrichment; the history mapper is not claimed to supply it. Fresh SSE success/failure controls at `:393` verify legitimate metrics/equity enrichment and fallback insertion remain possible. Existing reverse-order coverage is included in the supplied covering run.

## New breakage in the fix diff

- **None found.** The two production changes preserve the public hook/page interfaces, report identity, generation/read ownership, indexed-event dedup/resume and authoritative recovery replacement. No new Critical/Important regression identified within the four-file fix diff.

## Out-of-scope observations

- **None newly identified.** Prior whole-branch observations and controller-owned integration/privacy work remain with root; this review does not reopen them or extend the fix loop.

## Checks and evidence

- Read the scoped re-review instructions, fix brief, exact prior I1/I2 findings, fix report and complete packaged `review-8a745c40..3fab9117.diff` once. Reviewed bounded current-source context only in the two changed production files where the diff cut off ownership/recovery functions. No Git metadata queries, repository crawl, suite rerun, probe, subagent, network, live/account/provider call, or production/test/index/HEAD change.
- Inspected `/private/tmp/task-7-fix-covering.log`: the covering command names 15 relevant files, and its output records **15 passed files / 171 passed tests**, duration **7.42s**. The test diff supplies concrete regressions for both findings and positive controls. The implementer's reported exit 0 is consistent with the supplied output; no suite was independently rerun here.
- Inspected `/private/tmp/task-7-fix-build.log`: command is **`tsc -b && vite build`**, output records **3203 modules transformed** and **successful production build**. Existing large-chunk warning remains visible. Root owns the fresh integrated frontend/build rerun.
- Inspected RED logs: `/private/tmp/task-7-fix-red.log` records **6 failed / 47 passed**, including the clear-state failure; `/private/tmp/task-7-fix-cursor-red.log` records the targeted failure **expected event-1 resume, received event-9**, then **1 failed / 23 skipped**. These outputs support the report's baseline-failure claims without repeating the controlled probe.
- Review target: base **`8a745c4072a5cec58b3366fc72baa43b7c8d79d6`**, head **`3fab9117dce6f5b0631fc2ffdb7d1c69743a45b2`**, as supplied in the packaged artifact and fix report. No independent Git verification was requested or performed.

## Verdict

- **Fix round: All findings addressed, no new Critical/Important breakage.** I1 and I2 are closed within this scoped review. No additional fix loop requested. Overall branch readiness remains subject to root's integration gates and adjudication.
