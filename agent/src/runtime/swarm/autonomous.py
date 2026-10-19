"""Bounded peer workers pulling tasks from the durable blackboard."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from src.runtime.swarm import grounding
from src.runtime.swarm.autonomous_models import TaskLease, eligible
from src.runtime.swarm.blackboard import Blackboard
from src.runtime.swarm.models import RunStatus, SwarmRun, WorkerResult
from src.runtime.swarm.task_store import topological_layers
from src.runtime.tools.mcp import invalidate_mcp_specs_cache

logger = logging.getLogger(__name__)
_TERMINAL = {RunStatus.completed, RunStatus.failed, RunStatus.cancelled}


def _resume_results(runtime, run, previous):
    if previous is None:
        return {}
    from src.runtime.swarm.runtime import _agent_spec_changed, _rehome_artifact_paths, _task_definition_changed

    old_dir = runtime._store.run_dir(previous.id)
    board = Blackboard(old_dir)
    old_definition = board.definition() if board.initialized else previous
    previous = runtime._store.hydrate_run(previous)
    old_tasks = {t.id: t for t in old_definition.tasks}
    results = {t.id: t for t in previous.tasks}
    old_agents = {a.id: a for a in old_definition.agents}
    agents = {a.id: a for a in run.agents}
    kept = {}
    for layer in topological_layers(run.tasks):
        for task_id in layer:
            task = next(t for t in run.tasks if t.id == task_id)
            old = results.get(task_id)
            agent = agents.get(old.agent_id) if old else None
            old_agent = old_agents.get(old.agent_id) if old else None
            if (
                old is None
                or old.status != "completed"
                or task_id not in old_tasks
                or agent is None
                or old_agent is None
                or not eligible(agent, task)
                or _agent_spec_changed(old_agent, agent)
                or old_tasks[task_id].agent_id != task.agent_id
                or _task_definition_changed(
                    old_tasks[task_id].model_copy(update={"agent_id": old.agent_id}),
                    task.model_copy(update={"agent_id": old.agent_id}),
                    old_agents,
                    agents,
                    old_definition,
                    run,
                )
                or any(dep not in kept for dep in task.depends_on)
            ):
                continue
            artifacts = _rehome_artifact_paths(old.artifacts, old_dir, runtime._store.run_dir(run.id))
            kept[task_id] = task.model_copy(
                update={
                    "agent_id": old.agent_id,
                    "status": old.status,
                    "summary": old.summary,
                    "artifacts": artifacts,
                    "worker_iterations": old.worker_iterations,
                    "blocked_by": [],
                    "completed_at": old.completed_at,
                }
            )
    return kept


def _attempt_dir(run_dir: Path, lease: TaskLease) -> Path:
    root = run_dir.resolve()
    target = root / "artifacts" / "attempts" / lease.task.id / str(lease.epoch)
    if target.resolve() != target:
        raise ValueError("Attempt artifact directory must not traverse symlinks")
    target.mkdir(parents=True, exist_ok=True)
    return target


def _published_paths(result: WorkerResult, attempt_dir: Path, run_dir: Path) -> list[str]:
    published = []
    for raw in result.artifact_paths:
        relative = Path(raw)
        candidate = attempt_dir / relative
        if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "artifacts":
            continue
        try:
            candidate.resolve().relative_to(attempt_dir / "artifacts")
            candidate.resolve().relative_to(run_dir.resolve() / "artifacts")
        except ValueError:
            continue
        if candidate.is_file():
            published.append(candidate.relative_to(run_dir.resolve()).as_posix())
    return published


def execute_autonomous(runtime, run: SwarmRun, cancel: threading.Event, *, include_shell_tools=False, resume_from=None):
    """Own event delivery/final projection; each peer owns its claim decisions."""
    from src.runtime.swarm import runtime as runtime_module

    board = Blackboard(runtime._store.run_dir(run.id))
    stop = threading.Event()
    active_cancellations: set[threading.Event] = set()
    cancellation_lock = threading.Lock()
    cursor = 0
    try:
        if board.initialized:
            run = board.definition()
        board.initialize(
            run, max_workers=runtime._max_workers, completed_tasks=_resume_results(runtime, run, resume_from)
        )
        if cancel.is_set():
            board.cancel()
        invalidate_mcp_specs_cache()
        snapshot = board.snapshot(run)
        if snapshot.status not in _TERMINAL and snapshot.grounding_data is None:
            runtime._prefetch_grounding_data(run)
            board.set_grounding(run.grounding_data)
        grounding_block = grounding.format_grounding_block(board.snapshot(run).grounding_data or {})

        def peer(agent):
            while not stop.is_set() and not cancel.is_set():
                if board.snapshot(run).status in _TERMINAL:
                    return
                if not runtime._autonomous_slots.acquire(timeout=0.05):
                    continue
                try:
                    lease = board.claim(agent.id)
                    if lease is None:
                        stop.wait(0.03)
                        continue
                    done = threading.Event()
                    attempt_cancel = threading.Event()
                    with cancellation_lock:
                        active_cancellations.add(attempt_cancel)
                        if stop.is_set() or cancel.is_set():
                            attempt_cancel.set()

                    def renew(lease=lease, done=done, attempt_cancel=attempt_cancel):
                        while not done.wait(run.autonomy.lease_seconds / 3):
                            try:
                                if stop.is_set() or cancel.is_set() or not board.renew(lease):
                                    attempt_cancel.set()
                                    return
                            except Exception:
                                logger.exception("Lease renewal failed for %s/%s", run.id, lease.task.id)
                                attempt_cancel.set()
                                return

                    renewer = threading.Thread(target=renew, daemon=True, name=f"lease-{run.id}-{lease.task.id}")
                    renewer.start()
                    try:
                        attempt_dir = _attempt_dir(runtime._store.run_dir(run.id), lease)
                        tasks = {t.id: t for t in board.snapshot(run).tasks}
                        upstream = {key: tasks[tid].summary or "" for key, tid in lease.task.input_from.items()}
                        result = runtime_module.run_worker(
                            agent_spec=agent,
                            task=lease.task,
                            upstream_summaries=upstream,
                            user_vars=run.user_vars,
                            run_dir=attempt_dir,
                            event_callback=lambda event, lease=lease: board.append_event(event, lease),
                            include_shell_tools=include_shell_tools,
                            grounding_block=grounding_block,
                            cancel_event=attempt_cancel,
                            agent_config=runtime._agent_config,
                        )
                        result.artifact_paths = _published_paths(result, attempt_dir, runtime._store.run_dir(run.id))
                    except Exception as exc:
                        result = WorkerResult(status="failed", summary="", error=str(exc))
                    finally:
                        done.set()
                        renewer.join(timeout=1)
                        with cancellation_lock:
                            active_cancellations.discard(attempt_cancel)
                    board.complete(lease, result)
                except Exception as exc:
                    logger.exception("Autonomous peer failed for %s", run.id)
                    board.fail(str(exc))
                    return
                finally:
                    # Keep admission owned until the underlying worker really exits.
                    runtime._autonomous_slots.release()

        for agent in run.agents:
            threading.Thread(target=peer, args=(agent,), daemon=True, name=f"peer-{run.id}-{agent.id}").start()
        while True:
            if cancel.is_set():
                board.cancel()
            current = board.snapshot(run)
            events = board.read_events(cursor)
            for event in events:
                runtime._forward_event(run.id, event)
            cursor += len(events)
            if current.status in _TERMINAL:
                break
            stop.wait(0.03)
        runtime._store.update_run(current)
    except Exception as exc:
        logger.exception("Autonomous run failed for %s", run.id)
        if board.initialized:
            board.fail(str(exc))
        else:
            run.status = RunStatus.failed
            runtime._store.update_run(run)
    finally:
        stop.set()
        with cancellation_lock:
            for signal in active_cancellations:
                signal.set()
        with runtime._lock:
            if runtime._cancel_events.get(run.id) is cancel:
                runtime._cancel_events.pop(run.id, None)
                runtime._live_callbacks.pop(run.id, None)
