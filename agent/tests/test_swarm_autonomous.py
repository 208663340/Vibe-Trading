"""Exercise the real peer runtime with only the external model worker replaced."""

import threading
import time
import sqlite3
from types import SimpleNamespace

import pytest

import src.runtime.swarm.runtime as rt
from src.runtime.swarm.blackboard import Blackboard
from src.runtime.swarm.models import SwarmAgentSpec, SwarmRun, SwarmTask, WorkerResult
from src.runtime.swarm.store import SwarmStore
from src.runtime.swarm.worker import agent_artifact_dir


def run_spec(run_id="peer-runtime", *, tasks=None, agents=None, **settings):
    return SwarmRun(
        id=run_id,
        preset_name="peers",
        created_at="2026-10-19T00:00:00+00:00",
        user_vars={"label": run_id},
        execution_mode="autonomous",
        final_task_id="report",
        autonomy={"lease_seconds": 1, "timeout_seconds": 10, "max_attempts": 1, **settings},
        agents=agents
        or [
            SwarmAgentSpec(id="a", role="Research", system_prompt="x", capabilities=["research"]),
            SwarmAgentSpec(id="b", role="Research", system_prompt="x", capabilities=["research"]),
        ],
        tasks=tasks or [SwarmTask(id="report", prompt_template="research", required_capabilities=["research"])],
    )


def execute(tmp_path, monkeypatch, worker, run=None, max_workers=3):
    monkeypatch.setattr(rt, "run_worker", worker)
    store = SwarmStore(tmp_path)
    runtime = rt.SwarmRuntime(store, max_workers=max_workers)
    run = run or run_spec()
    store.create_run(run)
    runtime._execute_run(run, threading.Event())
    return store, runtime, store.load_run(run.id)


def wait_terminal(store, run_id):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        run = store.load_run(run_id)
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        threading.Event().wait(0.02)
    pytest.fail("Autonomous run did not become terminal")


def test_peer_claims_by_capability_and_publishes_final_report(tmp_path, monkeypatch):
    seen = []

    def worker(agent_spec, task, **kwargs):
        seen.append((agent_spec.id, task.id))
        return WorkerResult(status="completed", summary="evidence-based report", input_tokens=11, output_tokens=5)

    store, runtime, final = execute(tmp_path, monkeypatch, worker)
    assert final.status == "completed"
    assert final.final_report == "evidence-based report"
    assert len(seen) == 1
    assert final.total_input_tokens == 11
    assert final.tasks[0].agent_id in {"a", "b"}
    assert final.tasks[0].attempt == 1


def test_downstream_starts_while_unrelated_peer_is_still_working(tmp_path, monkeypatch):
    report_started = threading.Event()
    agents = run_spec().agents + [
        SwarmAgentSpec(id="editor", role="Editor", system_prompt="x", capabilities=["report"])
    ]
    run = run_spec(
        agents=agents,
        tasks=[
            SwarmTask(id="fast", prompt_template="fast", required_capabilities=["research"]),
            SwarmTask(id="slow", prompt_template="slow", required_capabilities=["research"]),
            SwarmTask(
                id="report",
                prompt_template="report",
                required_capabilities=["report"],
                depends_on=["fast"],
                input_from={"finding": "fast"},
            ),
        ],
    )

    def worker(task, upstream_summaries, **kwargs):
        if task.id == "slow":
            assert report_started.wait(3), "Ready report incorrectly waited for the slow task"
        elif task.id == "report":
            assert upstream_summaries == {"finding": "output-fast"}
            report_started.set()
        return WorkerResult(status="completed", summary=f"output-{task.id}")

    _, _, final = execute(tmp_path, monkeypatch, worker, run)
    assert final.status == "completed"
    assert report_started.is_set()


def test_attempt_outputs_are_isolated_and_only_winner_is_published(tmp_path, monkeypatch):
    outputs = []

    def worker(agent_spec, task, run_dir, **kwargs):
        artifact = agent_artifact_dir(run_dir, agent_spec.id, task.id) / "report.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"attempt-{task.attempt}")
        outputs.append(artifact)
        return WorkerResult(
            status="failed" if task.attempt == 1 else "completed",
            summary="winner",
            error="transient" if task.attempt == 1 else None,
            artifact_paths=[str(artifact.relative_to(run_dir))],
        )

    store, _, final = execute(tmp_path, monkeypatch, worker, run_spec(max_attempts=2))
    assert final.status == "completed"
    assert len(outputs) == 2 and outputs[0] != outputs[1]
    published = store.run_dir(final.id) / final.tasks[0].artifacts[0]
    assert published == outputs[1]
    assert published.read_text() == "attempt-2"


def test_recovery_reclaims_expired_work_without_replaying_completed_tasks(tmp_path, monkeypatch):
    run = run_spec(
        max_attempts=2,
        tasks=[
            SwarmTask(id="data", prompt_template="fetch"),
            SwarmTask(id="report", prompt_template="report", depends_on=["data"], input_from={"data": "data"}),
        ],
    )
    store = SwarmStore(tmp_path)
    store.create_run(run)
    before_crash = time.time() - 2
    board = Blackboard(store.run_dir(run.id), clock=lambda: before_crash)
    board.initialize(run, max_workers=2)
    data = board.claim("a")
    board.complete(data, WorkerResult(status="completed", summary="durable source"))
    old_report = board.claim("a")
    calls = []

    def worker(task, upstream_summaries, **kwargs):
        calls.append(task.id)
        assert upstream_summaries == {"data": "durable source"}
        return WorkerResult(status="completed", summary="recovered report")

    monkeypatch.setattr(rt, "run_worker", worker)
    runtime = rt.SwarmRuntime(store)
    runtime.recover_run(run.id)
    final = wait_terminal(store, run.id)
    assert final.status == "completed"
    assert calls == ["report"]
    assert not board.complete(old_report, WorkerResult(status="completed", summary="late stale answer"))
    assert store.load_run(run.id).final_report == "recovered report"


def test_cancel_is_durable_even_without_the_original_runtime(tmp_path, monkeypatch):
    run = run_spec()
    store = SwarmStore(tmp_path)
    store.create_run(run)
    board = Blackboard(store.run_dir(run.id))
    board.initialize(run, max_workers=1)
    lease = board.claim("a")
    runtime = rt.SwarmRuntime(store)
    assert runtime.cancel_run(run.id)
    assert store.load_run(run.id).status == "cancelled"
    assert not board.complete(lease, WorkerResult(status="completed", summary="late"))
    with pytest.raises(ValueError, match="terminal"):
        runtime.recover_run(run.id)


def test_run_reads_and_events_use_sqlite_after_json_projection_is_stale(tmp_path):
    run = run_spec()
    store = SwarmStore(tmp_path)
    store.create_run(run)
    board = Blackboard(store.run_dir(run.id))
    board.initialize(run, max_workers=1)
    board.complete(board.claim("a"), WorkerResult(status="completed", summary="durable final"))
    loaded = store.reconcile_run(store.load_run(run.id))
    assert loaded.status == "completed"
    assert loaded.final_report == "durable final"
    assert store.read_events(run.id)[-1].type == "run_completed"


def test_retry_resume_keeps_accepted_sources_and_rehomes_artifacts(tmp_path, monkeypatch):
    tasks = [
        SwarmTask(id="data", prompt_template="fetch"),
        SwarmTask(id="report", prompt_template="report", depends_on=["data"], input_from={"data": "data"}),
    ]
    previous = run_spec("previous", tasks=tasks)
    store = SwarmStore(tmp_path)
    old_dir = store.create_run(previous)
    board = Blackboard(old_dir)
    board.initialize(previous, max_workers=1)
    artifact = old_dir / "artifacts/a/data/report.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("accepted source")
    board.complete(
        board.claim("a"),
        WorkerResult(status="completed", summary="accepted source", artifact_paths=["artifacts/a/data/report.md"]),
    )
    board.complete(board.claim("a"), WorkerResult(status="failed", summary="", error="provider failed"))
    previous = store.load_run(previous.id)
    assert previous.status == "failed"
    fresh = run_spec("retry", tasks=tasks)
    store.create_run(fresh)
    calls = []

    def worker(task, upstream_summaries, **kwargs):
        calls.append(task.id)
        assert upstream_summaries == {"data": "accepted source"}
        return WorkerResult(status="completed", summary="fresh report")

    monkeypatch.setattr(rt, "run_worker", worker)
    runtime = rt.SwarmRuntime(store)
    runtime._execute_run(fresh, threading.Event(), resume_from=previous)
    final = store.load_run(fresh.id)
    assert final.status == "completed"
    assert calls == ["report"]
    assert (store.run_dir(fresh.id) / final.tasks[0].artifacts[0]).read_text() == "accepted source"
    assert artifact.read_text() == "accepted source"


def test_cancel_does_not_release_capacity_until_underlying_worker_exits(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    worker_cancellation = []

    def worker(user_vars, cancel_event, **kwargs):
        if user_vars["label"] == "first":
            worker_cancellation.append(cancel_event)
            started.set()
            release.wait(4)
        else:
            second_started.set()
        return WorkerResult(status="completed", summary="done")

    monkeypatch.setattr(rt, "run_worker", worker)
    store = SwarmStore(tmp_path)
    runtime = rt.SwarmRuntime(store, max_workers=1)
    first, second = run_spec("first", lease_seconds=30), run_spec("second")
    store.create_run(first)
    store.create_run(second)
    owner = threading.Thread(target=runtime._execute_run, args=(first, threading.Event()), daemon=True)
    owner.start()
    assert started.wait(2)
    try:
        assert runtime.cancel_run(first.id)
        owner.join(1)
        assert not owner.is_alive(), "Cancellation waited for an uncooperative tool"
        assert worker_cancellation[0].is_set(), "Cancellation incorrectly waited for the next lease renewal"
        next_owner = threading.Thread(target=runtime._execute_run, args=(second, threading.Event()), daemon=True)
        next_owner.start()
        assert not second_started.wait(0.15), "Cancelled tool released admission before it exited"
    finally:
        release.set()
    next_owner.join(3)
    assert store.load_run(first.id).status == "cancelled"
    assert store.load_run(first.id).final_report is None
    assert store.load_run(second.id).status == "completed"


def test_lease_is_renewed_while_worker_is_busy(tmp_path, monkeypatch):
    def worker(**kwargs):
        threading.Event().wait(1.2)
        return WorkerResult(status="completed", summary="slow but healthy")

    _, _, final = execute(tmp_path, monkeypatch, worker)
    assert final.status == "completed"
    assert final.tasks[0].attempt == 1


def test_live_callbacks_deliver_each_committed_event_once(tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "run_worker", lambda **kwargs: WorkerResult(status="completed", summary="done"))
    run = run_spec()
    store = SwarmStore(tmp_path)
    store.create_run(run)
    runtime = rt.SwarmRuntime(store)
    seen = []
    runtime._live_callbacks[run.id] = seen.append
    runtime._execute_run(run, threading.Event())
    assert [e.model_dump() for e in seen] == [e.model_dump() for e in store.read_events(run.id)]


def test_real_worker_contract_runs_with_only_provider_replaced(tmp_path, monkeypatch):
    import src.runtime.swarm.worker as worker_module
    from src.runtime.agent.tools import ToolRegistry
    from src.runtime.providers.chat import LLMResponse

    class Provider:
        def stream_chat(self, messages, tools=None, **kwargs):
            return LLMResponse(content="A completed research summary based on the supplied context.", tool_calls=[])

        def close(self):
            pass

    monkeypatch.setattr(worker_module, "ChatLLM", lambda **kwargs: Provider())
    monkeypatch.setattr(worker_module, "build_swarm_registry", lambda *args, **kwargs: ToolRegistry())
    run = run_spec()
    store = SwarmStore(tmp_path)
    store.create_run(run)
    rt.SwarmRuntime(store)._execute_run(run, threading.Event())
    final = store.load_run(run.id)
    assert final.status == "completed", [t.error for t in final.tasks]
    assert final.final_report.startswith("A completed research summary")


def test_uninitialized_database_does_not_break_pending_run_reads(tmp_path):
    run = run_spec()
    store = SwarmStore(tmp_path)
    run_dir = store.create_run(run)
    # A process can die immediately after SQLite creates the file, before DDL commits.
    sqlite3.connect(run_dir / "blackboard.sqlite3").close()
    assert store.load_run(run.id).status == "pending"
    assert store.read_events(run.id) == []
    runtime = rt.SwarmRuntime(store)
    assert runtime.cancel_run(run.id)
    assert store.load_run(run.id).status == "cancelled"


def test_cancel_before_execution_never_calls_a_worker(tmp_path, monkeypatch):
    run = run_spec()
    store = SwarmStore(tmp_path)
    store.create_run(run)
    calls = []
    monkeypatch.setattr(rt, "run_worker", lambda **kwargs: calls.append(kwargs))
    cancel = threading.Event()
    cancel.set()
    rt.SwarmRuntime(store)._execute_run(run, cancel)
    assert calls == []
    assert store.load_run(run.id).status == "cancelled"


def test_recovery_rejects_changed_model_instead_of_mislabeling_provenance(tmp_path, monkeypatch):
    run = run_spec().model_copy(update={"provider": "openai", "model": "saved-model"})
    store = SwarmStore(tmp_path)
    store.create_run(run)
    Blackboard(store.run_dir(run.id)).initialize(run, max_workers=1)
    monkeypatch.setattr(
        rt,
        "get_env_config",
        lambda: SimpleNamespace(llm=SimpleNamespace(langchain_provider="openai", langchain_model_name="changed-model")),
    )
    with pytest.raises(ValueError, match="model"):
        rt.SwarmRuntime(store).recover_run(run.id)
    assert store.load_run(run.id).status == "running"


def test_late_worker_callback_cannot_borrow_a_later_tasks_lease(tmp_path, monkeypatch):
    from src.runtime.swarm.models import SwarmEvent

    saved = []

    def worker(task, event_callback, **kwargs):
        if task.id == "data":
            saved.append(event_callback)
        else:
            saved[0](
                SwarmEvent(type="late_old_worker", agent_id="a", task_id="data", timestamp="2026-10-19T00:00:00+00:00")
            )
        return WorkerResult(status="completed", summary="done")

    run = run_spec(
        agents=[run_spec().agents[0]],
        tasks=[
            SwarmTask(id="data", prompt_template="fetch"),
            SwarmTask(id="report", prompt_template="report", depends_on=["data"]),
        ],
    )
    store, _, final = execute(tmp_path, monkeypatch, worker, run, max_workers=1)
    assert final.status == "completed"
    assert "late_old_worker" not in [e.type for e in store.read_events(run.id)]


def test_history_listing_uses_authoritative_state_before_json_projection(tmp_path):
    run = run_spec()
    store = SwarmStore(tmp_path)
    store.create_run(run)
    board = Blackboard(store.run_dir(run.id))
    board.initialize(run, max_workers=1)
    assert store.list_runs()[0].status == "running"
    lease = board.claim("a")
    board.complete(lease, WorkerResult(status="completed", summary="durable final"))
    listed = store.list_runs()[0]
    assert listed.status == "completed"
    assert listed.final_report == "durable final"
