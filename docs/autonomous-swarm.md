# Autonomous research swarm

This optional mode runs peers that independently claim ready tasks by capability.
There is no model supervisor. A shared local SQLite blackboard coordinates the
peers. Existing presets continue using the original DAG scheduler.

## Run it

Use the existing configured model/provider, then:

```bash
vibe-trading --swarm-run autonomous_research_team '{"topic":"Assess evidence for and against a momentum hypothesis, including costs and missing validation"}'
```

The bundled preset has two interchangeable researchers and one evidence reviewer.
They research supporting/opposing evidence, review the findings and synthesize a
report. It uses web/market research tools and does not place orders or execute shell
commands. Qualitative review is not a backtest or statistical acceptance gate.
Ordinary provider and data-source credentials/configuration still apply.

The same preset works with the existing `run_swarm` tool, preset inspection and REST:

```http
POST /swarm/runs
Content-Type: application/json
Authorization: Bearer YOUR_CONFIGURED_API_KEY

{"preset_name":"autonomous_research_team","user_vars":{"topic":"Your bounded research question"}}
```

Use `GET /swarm/runs/{id}` and `GET /swarm/runs/{id}/events` for progress. EventSource
ticket authentication and `Last-Event-ID` retain their existing semantics. Task
details include `required_capabilities`, the actual `agent_id`, `attempt`,
`lease_epoch` and `lease_expires_at`. An unclaimed task may have an empty `agent_id`.

## Recover, cancel and retry

After a host restart, authenticated clients can rejoin a nonterminal run:

```http
POST /swarm/runs/{id}/recover
Authorization: Bearer YOUR_CONFIGURED_API_KEY
```

Recovery keeps the same ID, accepted outputs, original deadline, retry counts and
valid leases. A live lease must expire before another peer can take over. Starting
the same run twice in one runtime returns 409; independent processes share atomic
admission through SQLite. Recovery rejects changed captured model/provider settings
and terminal runs. Unknown runs return 404. Shell access still follows the current
entry point's trusted configuration and is disabled by default on HTTP/MCP.

Each runtime bounds its own underlying workers; SQLite bounds valid leases across
processes. An expired worker in another process may still be executing until its
tool exits. Use one runtime when a strict host-wide physical worker quota is needed.

`POST /swarm/runs/{id}/cancel` durably fences current claims, including when the
original runtime has exited. Running Python tools are cancelled cooperatively;
their local admission slots stay occupied until they really exit. Their late
outputs cannot overwrite the terminal result.

`POST /swarm/runs/{id}/retry` creates a fresh run and budget. `?resume=true` carries
over compatible completed tasks/artifacts and re-executes the remaining subgraph.
Changed task definitions, capabilities, agent settings or effective model metadata
invalidate affected carried results and their dependents.

## Create a preset

User presets live in `~/.vibe-trading/swarm/presets/`. Minimal shape:

```yaml
name: my_peer_research
execution_mode: autonomous
final_task_id: report
autonomy:
  lease_seconds: 30       # 1..300
  timeout_seconds: 1800   # 1..86400; recovery never resets this deadline
  max_attempts: 2         # 1..10; includes expired/crashed attempts
agents:
  - id: researcher_a
    role: Researcher
    capabilities: [research]
    system_prompt: Use tools for dated evidence. State missing information.
    tools: [web_search, read_url]
  - id: researcher_b
    role: Researcher
    capabilities: [research]
    system_prompt: Use tools for dated evidence. State missing information.
    tools: [web_search, read_url]
tasks:
  - id: report
    required_capabilities: [research]
    prompt_template: "Research {topic} and report sourced findings and limitations."
variables:
  - name: topic
    required: true
```

All required capabilities must match. Optional task `agent_id` pins a task to a
specific peer. Capability labels do not grant tools, skills or credentials. Agent
tool allowlists continue to be enforced by the existing registry. Declare every
`input_from` source in `depends_on`. Cycles, duplicate/unsafe IDs, missing eligible
peers, missing final tasks and invalid limits are rejected before execution. At
most 32 agents and 256 tasks may participate in a run. In this mode `max_attempts`
owns retries; the legacy agent-level `max_retries` controls only DAG execution.

## Storage and operating limits

Each run adds `blackboard.sqlite3` with schema version 1. SQLite task transitions
and ordered events commit in one transaction. A stale run.json is only a projection;
do not edit/copy individual database files while a run is active. Store the database
on a local filesystem, not NFS/SMB. An unknown future schema version is rejected.

Attempt artifacts live under `artifacts/attempts/{task}/{epoch}/`; only the current
lease can publish their paths. Fencing cannot reverse external side effects: custom
presets should use idempotent/research tools, and any trading writes still require
the existing trading enforcement. Crash-lost token usage is not billed-accounting
data. This mode does not yet generate new tasks, learn routing, enforce an exact
token budget, or certify a strategy's out-of-sample performance.
