# ADR-001: Explicit runtime ownership with compatible boundaries

Status: Accepted and verified locally on `codex/architecture-stability-upgrade`.
Date: 2026-10-13.
Verification date: 2026-10-14; code revision `3fab9117dce6f5b0631fc2ffdb7d1c69743a45b2`.

## Context

Independent frontend, backend and Agent audits reproduced session cancellation leaks,
late UI state commits, stale replay across writes and blocking provider reads. The
existing feature set and external contracts are extensive, so a framework rewrite
would expand risk without addressing these concrete ownership failures by itself.

## Decision

Preserve FastAPI, filesystem/SQLite, React/Zustand and the custom AgentLoop. Introduce
explicit owners for session attempts, API resources, UI operations, provider producers,
tool admission and run terminal transitions. Separate authoritative persistence from
rebuildable projections, and completion observation from ordered evidence commits.
Producer admission stays bounded until underlying work actually exits; cancellation
does not pretend Python threads or server-side writes were forcefully terminated.

## Alternatives

- Replace the Agent framework or split into microservices: rejected because the audited
  defects arise from ownership/order, and schema migration would add unrelated risk.
- Add isolated conditional guards to existing large methods: rejected as the sole
  strategy because it leaves lifecycle responsibility spread across unrelated callers.
- Introduce a new database/queue/state library: deferred until multi-process or durable
  distributed job requirements exist. This upgrade retains single-process ownership.

## Consequences

Public API/tool/event facades stay compatible while internal modules become separately
testable. Active deletion becomes a documented conflict; closing services reject new
work. Abandoned external producers can remain alive, but cannot emit late UI/evidence
updates or grow unbounded. Future distributed execution will require a separate
storage-owner protocol. Focused red/green tests and independent reviews protect each
boundary; full non-live regression is the final integration gate.

All six implementation tasks passed independent review after their recorded fixes.
The complete branch review found two frontend ordering defects; one consolidated fix
and scoped re-review closed both. Backend 18,026 tests and frontend 787 tests passed;
85 backend skips and existing warnings remain documented. The branch stays local and
unmerged. Commands, evidence and the precise thread, callback and filesystem limits
are in the [verification report](../architecture/2026-10-14-stability-verification.md).
