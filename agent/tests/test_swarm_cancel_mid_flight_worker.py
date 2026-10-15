"""Regression: cancel_run() must stop an already-dispatched worker promptly,
not just skip layers that have not started yet.

Pre-fix: cancel_event was threaded from cancel_run() into _execute_run and
_execute_layer, and polled between layers, but was never passed into
_run_worker_with_retries or run_worker. A worker already running when
cancel_run() fired ran to completion untouched, exactly the gap AgentLoop's
should_cancel mechanism (agent/src/runtime/agent/loop.py) already closed for the
main, non-swarm agent loop.

Post-fix: cancel_event reaches run_worker(), is passed to
ChatLLM.stream_chat() as should_cancel (interrupting an in-flight LLM
stream), and is checked at the top of each ReAct iteration and right after
the stream returns, mirroring AgentLoop's cooperative-cancellation
contract: an in-flight stream stops promptly and that turn's tool calls
are skipped, but a tool call already executing is not interrupted.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import src.runtime.swarm.runtime as rt
from src.runtime.swarm.models import SwarmAgentSpec, SwarmRun, SwarmTask, WorkerResult
from src.runtime.swarm.store import SwarmStore


def _make_run(tmp_path: Path):
    store = SwarmStore(base_dir=tmp_path)
    runtime = rt.SwarmRuntime(store=store)
    agents = [SwarmAgentSpec(id="a", role="x", system_prompt="x", max_retries=0)]
    tasks = [SwarmTask(id="t1", agent_id="a", prompt_template="do x")]
    run = SwarmRun(
        id="r-cancel",
        preset_name="demo",
        created_at="2026-05-27T18:30:56+00:00",
        agents=agents,
        tasks=tasks,
    )
    store.create_run(run)
    return store, runtime, run


def test_cancel_mid_flight_worker_stops_promptly(tmp_path, monkeypatch):
    def slow_worker(agent_spec, task, **kwargs):
        cancel_event = kwargs.get("cancel_event")
        for _ in range(150):
            if cancel_event is not None and cancel_event.is_set():
                return WorkerResult(status="cancelled", summary="cancelled")
            time.sleep(0.01)
        return WorkerResult(status="completed", summary="done")

    monkeypatch.setattr(rt, "run_worker", slow_worker)
    store, runtime, run = _make_run(tmp_path)
    cancel_event = threading.Event()

    def _cancel_soon():
        time.sleep(0.1)
        cancel_event.set()

    threading.Thread(target=_cancel_soon).start()
    t0 = time.monotonic()
    runtime._execute_run(run, cancel_event)
    elapsed = time.monotonic() - t0

    assert elapsed < 0.5, (
        f"cancel_run() did not reach the in-flight worker promptly: run took "
        f"{elapsed:.2f}s despite cancel_event being set after 0.1s."
    )


def test_run_worker_receives_cancel_event_kwarg(tmp_path, monkeypatch):
    """The retry wrapper must actually forward cancel_event to run_worker,
    not just accept it."""
    received = {}

    def capture_worker(agent_spec, task, **kwargs):
        received["cancel_event"] = kwargs.get("cancel_event")
        return WorkerResult(status="completed", summary="done")

    monkeypatch.setattr(rt, "run_worker", capture_worker)
    store, runtime, run = _make_run(tmp_path)
    cancel_event = threading.Event()

    runtime._execute_run(run, cancel_event)

    assert received["cancel_event"] is cancel_event


def test_cancelled_worker_result_persists_as_cancelled_task(tmp_path, monkeypatch):
    """A worker that stopped because cancel_run() fired must land as a
    *cancelled* task with a task_cancelled event — the generic "worker did
    not complete" branch would otherwise relabel a user stop as a failure."""
    from src.runtime.swarm.models import RunStatus, TaskStatus
    from src.runtime.swarm.task_store import TaskStore

    def cancelling_worker(agent_spec, task, **kwargs):
        kwargs["cancel_event"].set()
        return WorkerResult(status="cancelled", summary="stopped on request", iterations=2)

    monkeypatch.setattr(rt, "run_worker", cancelling_worker)
    store, runtime, run = _make_run(tmp_path)
    cancel_event = threading.Event()

    runtime._execute_run(run, cancel_event)

    task = TaskStore(store.run_dir(run.id)).load_task("t1")
    assert task.status == TaskStatus.cancelled, task.status
    assert not task.error, f"a cancelled task must carry no failure error, got {task.error!r}"
    assert task.worker_iterations == 2

    event_types = [e.type for e in store.read_events(run.id)]
    assert "task_cancelled" in event_types, event_types
    assert "task_failed" not in event_types, event_types

    assert store.load_run(run.id).status == RunStatus.cancelled
