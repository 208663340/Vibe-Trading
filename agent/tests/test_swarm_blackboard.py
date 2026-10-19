"""Durable peer coordination: use real SQLite connections, not mocked locks."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys
import sqlite3

import pytest

from src.runtime.swarm.blackboard import Blackboard
from src.runtime.swarm.models import SwarmAgentSpec, SwarmRun, SwarmTask, WorkerResult


@dataclass
class Clock:
    now: float = 1000.0

    def __call__(self):
        return self.now


def make_run(*, tasks=None, **settings):
    return SwarmRun(
        id="peer-run",
        preset_name="peers",
        created_at="2026-10-19T00:00:00+00:00",
        execution_mode="autonomous",
        final_task_id="report",
        autonomy={"lease_seconds": 3, "timeout_seconds": 60, "max_attempts": 2, **settings},
        agents=[
            SwarmAgentSpec(id="a", role="Research", system_prompt="x", capabilities=["research"]),
            SwarmAgentSpec(id="b", role="Research", system_prompt="x", capabilities=["research"]),
        ],
        tasks=tasks or [SwarmTask(id="report", prompt_template="research", required_capabilities=["research"])],
    )


def board_at(tmp_path, clock, run=None, max_workers=2):
    board = Blackboard(tmp_path, clock=clock)
    board.initialize(run or make_run(), max_workers=max_workers)
    return board


def success(text="verified output"):
    return WorkerResult(status="completed", summary=text, input_tokens=7, output_tokens=3)


def test_independent_connections_cannot_claim_the_same_task(tmp_path):
    clock = Clock()
    board_at(tmp_path, clock)

    def claim(agent):
        return Blackboard(tmp_path, clock=clock).claim(agent)

    with ThreadPoolExecutor(2) as pool:
        claims = list(pool.map(claim, ["a", "b"]))
    assert sum(item is not None for item in claims) == 1
    winner = next(item for item in claims if item is not None)
    assert winner.task.id == "report"


def test_separate_processes_cannot_claim_the_same_task(tmp_path):
    board_at(tmp_path, Clock())
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from src.runtime.swarm.blackboard import Blackboard
lease = Blackboard(Path(sys.argv[1]), clock=lambda: 1000.0).claim(sys.argv[2])
print(lease.task.id if lease else 'none')
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(tmp_path), agent, str(Path(__file__).resolve().parents[1])],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for agent in ("a", "b")
    ]
    outputs = [child.communicate(timeout=10) for child in children]
    assert [child.returncode for child in children] == [0, 0], outputs
    assert sorted(out.strip() for out, _ in outputs) == ["none", "report"]


def test_expired_owner_cannot_renew_or_overwrite_new_result(tmp_path):
    clock = Clock()
    run = make_run()
    first = board_at(tmp_path, clock, run)
    stale = first.claim("a")
    clock.now += 4
    second = Blackboard(tmp_path, clock=clock)
    current = second.claim("b")
    assert current.epoch > stale.epoch
    assert not first.renew(stale)
    assert second.complete(current, success("winner"))
    assert not first.complete(stale, success("stale"))
    loaded = first.snapshot(run)
    assert loaded.tasks[0].summary == "winner"
    assert loaded.tasks[0].agent_id == "b"
    assert loaded.total_input_tokens == 7


def test_duplicate_completion_does_not_double_count_tokens_or_events(tmp_path):
    clock = Clock()
    run = make_run()
    board = board_at(tmp_path, clock, run)
    lease = board.claim("a")
    assert board.complete(lease, success())
    events = board.read_events()
    assert not board.complete(lease, success())
    assert board.read_events() == events
    assert board.snapshot(run).total_output_tokens == 3


def test_live_lease_survives_reinitialization_and_keeps_deadline(tmp_path):
    clock = Clock()
    run = make_run()
    board = board_at(tmp_path, clock, run)
    lease = board.claim("a")
    clock.now += 1
    recovered = board_at(tmp_path, clock, run)
    assert recovered.claim("b") is None
    assert recovered.renew(lease)
    clock.now = 1060
    assert not recovered.complete(lease, success())
    assert recovered.snapshot(run).status == "failed"


def test_cancel_fences_all_inflight_work_and_is_terminal(tmp_path):
    clock = Clock()
    run = make_run()
    board = board_at(tmp_path, clock, run)
    lease = board.claim("a")
    board.cancel()
    board.initialize(run, max_workers=2)
    assert board.claim("b") is None
    assert not board.complete(lease, success())
    assert not board.renew(lease)
    assert board.snapshot(run).status == "cancelled"
    assert [e.type for e in board.read_events()].count("run_completed") == 1


def test_failed_dependency_never_releases_downstream(tmp_path):
    clock = Clock()
    run = make_run(
        max_attempts=1,
        tasks=[
            SwarmTask(id="source", prompt_template="fetch", required_capabilities=["research"]),
            SwarmTask(id="report", prompt_template="report", depends_on=["source"], input_from={"source": "source"}),
        ],
    )
    board = board_at(tmp_path, clock, run)
    lease = board.claim("a")
    assert board.complete(lease, WorkerResult(status="failed", summary="", error="source unavailable"))
    assert board.claim("b") is None
    loaded = board.snapshot(run)
    assert loaded.status == "failed"
    assert loaded.tasks[1].status == "blocked"
    assert loaded.tasks[1].started_at is None


def test_ready_dependents_do_not_wait_for_unrelated_tasks(tmp_path):
    clock = Clock()
    run = make_run(
        tasks=[
            SwarmTask(id="fast", prompt_template="fast"),
            SwarmTask(id="slow", prompt_template="slow"),
            SwarmTask(id="report", prompt_template="report", depends_on=["fast"]),
        ]
    )
    board = board_at(tmp_path, clock, run)
    fast = board.claim("a")
    slow = board.claim("b")
    assert board.complete(fast, success())
    downstream = board.claim("a")
    assert downstream.task.id == "report"
    assert board.snapshot(run).tasks[1].status == "in_progress"
    assert board.complete(slow, success())


def test_capacity_and_agent_capabilities_are_enforced(tmp_path):
    clock = Clock()
    run = make_run(tasks=[SwarmTask(id="one", prompt_template="1"), SwarmTask(id="report", prompt_template="2")])
    board = board_at(tmp_path, clock, run, max_workers=1)
    lease = board.claim("a")
    assert board.claim("b") is None
    assert board.claim("a") is None
    assert board.complete(lease, success())
    assert board.claim("b").task.id == "report"
    with pytest.raises(ValueError, match="Unknown agent"):
        board.claim("intruder")


def test_retries_stop_after_durable_attempt_limit(tmp_path):
    clock = Clock()
    run = make_run()
    board = board_at(tmp_path, clock, run)
    first = board.claim("a")
    board.complete(first, WorkerResult(status="timeout", summary="", error="timeout"))
    second = board.claim("b")
    assert second.task.attempt == 2
    clock.now += 4
    assert board.claim("a") is None
    assert board.snapshot(run).status == "failed"
    assert "attempt" in board.snapshot(run).tasks[0].error.lower()


def test_events_are_durable_and_resume_by_cursor(tmp_path):
    clock = Clock()
    run = make_run()
    board = board_at(tmp_path, clock, run)
    cursor = len(board.read_events())
    lease = board.claim("a")
    board.complete(lease, success())
    other = Blackboard(tmp_path, clock=clock)
    tail = other.read_events(after_index=cursor)
    assert tail[0].type == "task_started"
    assert tail[-1].type == "run_completed"
    assert other.snapshot(run).final_report == "verified output"


def test_unknown_database_schema_is_rejected_without_rewriting_state(tmp_path):
    clock = Clock()
    board = board_at(tmp_path, clock)
    with sqlite3.connect(board.path) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="schema"):
        board.initialize(make_run(), max_workers=2)


@pytest.mark.parametrize(
    "change, message",
    [
        ({"agents": []}, "agent"),
        ({"final_task_id": "missing"}, "final_task_id"),
        ({"tasks": [SwarmTask(id="report", prompt_template="x", required_capabilities=["missing"])]}, "eligible"),
        ({"tasks": [SwarmTask(id="../report", prompt_template="x")]}, "segment"),
    ],
)
def test_invalid_runs_are_rejected_before_initialization(tmp_path, change, message):
    run = make_run().model_copy(update=change)
    with pytest.raises(ValueError, match=message):
        board_at(tmp_path, Clock(), run)
