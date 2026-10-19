# Autonomous swarm: peer task claiming

## Scope

Add an opt-in `autonomous` execution mode alongside the existing `dag` mode.
Peers independently claim ready tasks by capability. No model decides who runs
next. The first release uses a bounded, preset-defined task graph; model-created
tasks, scientific peer acceptance, and learned routing are separate increments.

## Runtime and framework choice

Reuse the existing Swarm Worker, provider adapters, tool/skill allowlists, FastAPI
and SSE. The interactive AgentLoop already hosts Pi Agent Core; this change does
not migrate the separate Swarm Worker loop or introduce a second agent framework.
Use Python's SQLite for the collaboration protocol. LangGraph checkpoints handle
graph execution, not our cross-worker lease and result fencing contract. Strands
Swarm offers autonomous handoffs, which do not by themselves provide this protocol.
Temporal would introduce a service dependency beyond the local desktop deployment.

## Contracts

- `dag` remains the default for old presets and serialized runs.
- Autonomous presets declare agent `capabilities`, task `required_capabilities`,
  an explicit `final_task_id`, and bounded `autonomy` settings.
- A task may pin `agent_id`; otherwise any peer satisfying every required
  capability may claim it. Capability labels never grant tools or credentials.
- A per-run SQLite database owns task states, leases, token totals, terminal state,
  deadline and an ordered event log. JSON run metadata is a rebuildable projection.
- `BEGIN IMMEDIATE` serializes task admission. Claims check dependencies, worker
  identity, capacity, retry limits, run cancellation and the durable deadline.
- Each claim has a monotonically increasing epoch. Renewal and result commits
  require the same owner/epoch and an unexpired lease. Late or duplicate results
  cannot change accepted outputs or release dependencies.
- Each attempt gets a distinct artifact directory under the run's `artifacts/`.
  Fencing prevents stale publication; external side effects remain at-least-once.
  The bundled preset uses research/file tools, with no order-placement tools.
- Peer execution is bounded by a runtime-wide semaphore held until underlying
  work really exits. Cancellation does not pretend to kill Python threads.
- Restart recovery joins the same database, preserves the original deadline and
  valid leases, and only reclaims expired attempts. A cancelled/finished run cannot
  be recovered. A changed captured model/provider configuration requires retry.
  Existing retry creates a new run with a fresh budget.
- Completion means the existing Worker output contract was met. It is not a
  certificate of strategy validity, independent review, or financial performance.

## Engineering verification

Race independent database connections/processes for a single claim; reject stale
owners after lease expiry; preserve dependency gating on failures; reject duplicate
completion; keep terminal cancellation immutable; isolate attempt artifacts; recover
after restart without rerunning accepted work; preserve SSE event cursors and legacy
DAG behavior. No real market/model/broker calls are needed for these tests.

## Deployment limits

This is single-host coordination on a local SQLite filesystem, not multi-host P2P.
Do not place the database on a network filesystem. SQLite events are authoritative
for autonomous runs. Original task definitions/agent specs remain immutable during
a run. Wall-clock deadline and attempt count bound work; exact token/cost caps,
dynamic task publication and independent evidence acceptance are not implemented.

## Sources

- [Pi Python runtime](https://github.com/encyc/pi-py)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [Strands Swarm](https://strandsagents.com/docs/user-guide/sdk/multi-agent/swarm/)
- [Temporal workflows](https://docs.temporal.io/workflows)
