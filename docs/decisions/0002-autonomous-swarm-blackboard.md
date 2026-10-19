# ADR-002: Transactional blackboard for autonomous research peers

Status: Accepted for the opt-in first phase; verification recorded separately.
Date: 2026-10-19.

## Context

Swarm currently binds tasks to agents and schedules complete topological layers.
The requested collaboration model lets capable peers independently claim ready
work, continue after another peer fails, and publish results without a model acting
as a permanent supervisor. Desktop and local CLI deployments must stay usable.
The existing interactive runtime uses Pi Agent Core; Swarm has a separate Worker.

## Decision

Add `execution_mode: autonomous` to trusted YAML presets; existing presets default
to `dag`. Reuse Worker/provider/tool/skill boundaries. A per-run SQLite database
owns claims, monotonic lease epochs, attempt limits, deadline, results and events.
Tasks become eligible when their own prerequisites complete, removing layer barriers.
SQLite transactions choose a single claimant; agent capability sets determine who
is eligible. A runtime semaphore stays held until underlying work exits.

Recover the same run through an authenticated endpoint without resetting leases
or deadline. Reject recovery with a different captured model/provider configuration;
retry creates a new run and budget. Every attempt writes into a separate directory.
Legacy JSON state and JSONL events remain the DAG storage format. Autonomous reads
and SSE use SQLite, so a crash before writing a JSON projection loses no committed
task result or cursor position. Database schema version 1 fails closed on unknown
future versions. Interrupted empty-file initialization remains recoverable.

## Alternatives

- LangGraph: useful for explicit stateful graphs and checkpoints; introducing it
  here would add a second scheduling boundary while leases/fencing still require a
  domain protocol. The dependency already present in the project is not a reason
  to migrate the existing execution loop.
- Strands Swarm: suitable for autonomous agent handoffs; its handoff pattern alone
  does not implement our durable concurrent-claim and stale-result contract.
- Temporal: appropriate when durable multi-host orchestration is required; deferred
  because it adds a service and operational model to a local desktop deployment.
- File locks: insufficient for transactional claims, lease expiry, result fencing
  and ordered event publication across independent processes.

## Consequences

No new dependencies or external services. SQLite requires a local filesystem and
is not the chosen multi-host transport. Peer decisions are distributed while storage
remains shared. First-phase tasks are preset-defined; claim selection is a local
capability policy, not an LLM task-market negotiation. Model-created tasks, learned
specialization and independent scientific acceptance remain future increments.

External tool effects are at-least-once: fencing prevents publishing stale results,
not undoing a network request or an order. The bundled preset grants research tools
only. Existing trading authorization and enforcement remain owned by their modules.
Wall time and attempts bound execution. Reported token totals count accepted attempt
results; they cannot account exactly for work lost during a process crash.

See [design](../architecture/2026-10-19-autonomous-swarm.md) and
[operator guide](../autonomous-swarm.md).
