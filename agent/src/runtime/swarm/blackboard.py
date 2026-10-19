"""Single-host peer coordination with transactional leases and fenced results.

The SQLite database is authoritative. Run JSON and UI state may be rebuilt from
it; task transitions and their events commit together. Never share this database
over a network filesystem. External tool side effects are still at-least-once.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.runtime.swarm.autonomous_models import TaskLease, eligible, validate_autonomous_run
from src.runtime.swarm.models import RunStatus, SwarmEvent, SwarmRun, SwarmTask, TaskStatus, WorkerResult
from src.runtime.tools.redaction import redact_internal_paths


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class Blackboard:
    """One connection per operation, safe across threads and local processes."""

    def __init__(self, run_dir: Path, *, clock: Callable[[], float] = time.time):
        self.path = run_dir / "blackboard.sqlite3"
        self.clock = clock

    @property
    def initialized(self) -> bool:
        """An empty file from interrupted initialization is not a live board."""
        if not self.path.is_file():
            return False
        with closing(sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
            try:
                return db.execute("SELECT 1 FROM run WHERE id=1").fetchone() is not None
            except sqlite3.OperationalError as exc:
                if "no such table: run" in str(exc):
                    return False
                raise

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            db.row_factory = sqlite3.Row
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise ValueError(f"Unsupported blackboard schema version: {version}")
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def initialize(
        self, run: SwarmRun, *, max_workers: int, completed_tasks: dict[str, SwarmTask] | None = None
    ) -> None:
        validate_autonomous_run(run)
        if not isinstance(max_workers, int) or not 1 <= max_workers <= 32:
            raise ValueError("max_workers must be between 1 and 32")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS run (
                id INTEGER PRIMARY KEY CHECK (id=1), definition TEXT NOT NULL,
                status TEXT NOT NULL, deadline REAL NOT NULL, max_workers INTEGER NOT NULL,
                completed_at TEXT, input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0, grounding TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, definition TEXT NOT NULL,
                state TEXT NOT NULL, owner TEXT, epoch INTEGER NOT NULL DEFAULT 0,
                expires REAL)""")
            db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL)")
            db.execute("PRAGMA user_version=1")
            existing = db.execute("SELECT definition FROM run WHERE id=1").fetchone()
            if existing:
                if json.loads(existing[0])["id"] != run.id:
                    raise ValueError("Blackboard belongs to another run")
                return
            now = self.clock()
            db.execute(
                "INSERT INTO run(id, definition, status, deadline, max_workers, grounding) VALUES(1, ?, 'running', ?, ?, ?)",
                (
                    run.model_dump_json(),
                    now + run.autonomy.timeout_seconds,
                    max_workers,
                    json.dumps(run.grounding_data),
                ),
            )
            for ordinal, task in enumerate(run.tasks):
                state = task.model_copy(
                    update={
                        "status": TaskStatus.blocked if task.depends_on else TaskStatus.pending,
                        "blocked_by": list(task.depends_on),
                    }
                )
                if task.id in (completed_tasks or {}):
                    state = completed_tasks[task.id].model_copy(
                        update={"attempt": 0, "lease_epoch": 0, "lease_expires_at": None}
                    )
                db.execute(
                    "INSERT INTO tasks(id, ordinal, definition, state) VALUES(?, ?, ?, ?)",
                    (task.id, ordinal, task.model_dump_json(), state.model_dump_json()),
                )
            self._event(db, "run_started", data={"execution_mode": "autonomous"})
            for task in (completed_tasks or {}).values():
                self._event(db, "task_resumed", agent_id=task.agent_id, task_id=task.id)

    def definition(self) -> SwarmRun:
        with self._transaction() as db:
            return SwarmRun.model_validate_json(self._run(db)["definition"])

    @staticmethod
    def _run(db):
        row = db.execute("SELECT * FROM run WHERE id=1").fetchone()
        if row is None:
            raise ValueError("Blackboard is not initialized")
        return row

    @staticmethod
    def _tasks(db) -> list[SwarmTask]:
        return [SwarmTask.model_validate_json(row[0]) for row in db.execute("SELECT state FROM tasks ORDER BY ordinal")]

    @staticmethod
    def _save(db, task: SwarmTask, *, owner=None, expires=None):
        db.execute(
            "UPDATE tasks SET state=?, owner=?, expires=? WHERE id=?", (task.model_dump_json(), owner, expires, task.id)
        )

    def _event(self, db, kind, *, agent_id=None, task_id=None, data=None):
        event = SwarmEvent(type=kind, agent_id=agent_id, task_id=task_id, data=data or {}, timestamp=_iso(self.clock()))
        db.execute("INSERT INTO events(event) VALUES(?)", (event.model_dump_json(),))

    def _finish(self, db, status: RunStatus):
        if self._run(db)["status"] != "running":
            return
        db.execute("UPDATE run SET status=?, completed_at=? WHERE id=1", (status.value, _iso(self.clock())))
        self._event(db, "run_completed", data={"status": status.value})

    def _stop(self, db, status: RunStatus, reason: str):
        for task in self._tasks(db):
            if task.status not in {TaskStatus.completed, TaskStatus.failed, TaskStatus.cancelled}:
                task.status = TaskStatus.cancelled
                task.error = reason
                task.completed_at = _iso(self.clock())
                task.lease_expires_at = None
                self._save(db, task)
                self._event(
                    db, "task_cancelled", agent_id=task.agent_id or None, task_id=task.id, data={"reason": reason}
                )
        self._finish(db, status)

    def _advance(self, db):
        run = self._run(db)
        if run["status"] != "running":
            return
        now = self.clock()
        if now >= run["deadline"]:
            self._stop(db, RunStatus.failed, "Autonomous run deadline exceeded")
            return
        definition = SwarmRun.model_validate_json(run["definition"])
        expired = db.execute(
            "SELECT state, definition FROM tasks WHERE owner IS NOT NULL AND expires<=?", (now,)
        ).fetchall()
        for row in expired:
            task = SwarmTask.model_validate_json(row["state"])
            original = SwarmTask.model_validate_json(row["definition"])
            task.lease_expires_at = None
            if task.attempt >= definition.autonomy.max_attempts:
                task.status = TaskStatus.failed
                task.error = "Lease expired; task attempt limit exhausted"
                task.completed_at = _iso(now)
                kind = "task_failed"
            else:
                task.status = TaskStatus.pending
                task.agent_id = original.agent_id
                task.error = "Lease expired; ready for takeover"
                kind = "task_retry"
            self._save(db, task)
            self._event(db, kind, task_id=task.id, data={"reason": task.error, "attempt": task.attempt})
        # Iterate to propagate failed prerequisites even when YAML is not in topological order.
        tasks = self._tasks(db)
        by_id = {t.id: t for t in tasks}
        for _ in tasks:
            changed = False
            for task in tasks:
                if task.status not in {TaskStatus.pending, TaskStatus.blocked}:
                    continue
                waiting = [dep for dep in task.depends_on if by_id[dep].status != TaskStatus.completed]
                failed = [
                    dep
                    for dep in waiting
                    if by_id[dep].status in {TaskStatus.failed, TaskStatus.cancelled}
                    or (by_id[dep].status == TaskStatus.blocked and by_id[dep].error)
                ]
                if failed:
                    reason = "Blocked: upstream not completed (" + ", ".join(failed) + ")"
                    if task.error != reason:
                        task.error = reason
                        task.status = TaskStatus.blocked
                        task.blocked_by = waiting
                        self._save(db, task)
                        self._event(
                            db,
                            "task_blocked",
                            agent_id=task.agent_id or None,
                            task_id=task.id,
                            data={"blocked_by": waiting, "reason": reason},
                        )
                        changed = True
                elif not waiting and task.status == TaskStatus.blocked:
                    task.status = TaskStatus.pending
                    task.blocked_by = []
                    self._save(db, task)
                    changed = True
            if not changed:
                break
        if all(t.status == TaskStatus.completed for t in tasks):
            self._finish(db, RunStatus.completed)
        elif not any(t.status in {TaskStatus.pending, TaskStatus.in_progress} for t in tasks):
            self._finish(db, RunStatus.failed)

    def claim(self, agent_id: str) -> TaskLease | None:
        with self._transaction() as db:
            run = self._run(db)
            definition = SwarmRun.model_validate_json(run["definition"])
            agent = next((a for a in definition.agents if a.id == agent_id), None)
            if agent is None:
                raise ValueError(f"Unknown agent {agent_id!r}")
            self._advance(db)
            if self._run(db)["status"] != "running":
                return None
            if db.execute("SELECT COUNT(*) FROM tasks WHERE owner IS NOT NULL").fetchone()[0] >= run["max_workers"]:
                return None
            if db.execute("SELECT 1 FROM tasks WHERE owner=?", (agent_id,)).fetchone():
                return None
            for row in db.execute("SELECT * FROM tasks ORDER BY ordinal").fetchall():
                task = SwarmTask.model_validate_json(row["state"])
                if task.status != TaskStatus.pending or not eligible(
                    agent, SwarmTask.model_validate_json(row["definition"])
                ):
                    continue
                expires = min(self.clock() + definition.autonomy.lease_seconds, run["deadline"])
                task.agent_id = agent_id
                task.status = TaskStatus.in_progress
                task.attempt += 1
                task.lease_epoch = row["epoch"] + 1
                task.lease_expires_at = _iso(expires)
                task.started_at = _iso(self.clock())
                task.completed_at = None
                task.error = None
                self._save(db, task, owner=agent_id, expires=expires)
                db.execute("UPDATE tasks SET epoch=? WHERE id=?", (task.lease_epoch, task.id))
                self._event(
                    db,
                    "task_started",
                    agent_id=agent_id,
                    task_id=task.id,
                    data={"attempt": task.attempt, "lease_epoch": task.lease_epoch},
                )
                return TaskLease(task, agent_id, task.lease_epoch)
            return None

    def _owns(self, db, lease: TaskLease):
        self._advance(db)
        if self._run(db)["status"] != "running":
            return None
        row = db.execute(
            "SELECT * FROM tasks WHERE id=? AND owner=? AND epoch=? AND expires>?",
            (lease.task.id, lease.agent_id, lease.epoch, self.clock()),
        ).fetchone()
        return SwarmTask.model_validate_json(row["state"]) if row else None

    def renew(self, lease: TaskLease) -> bool:
        with self._transaction() as db:
            task = self._owns(db, lease)
            if task is None:
                return False
            run = self._run(db)
            settings = SwarmRun.model_validate_json(run["definition"]).autonomy
            expires = min(self.clock() + settings.lease_seconds, run["deadline"])
            task.lease_expires_at = _iso(expires)
            self._save(db, task, owner=lease.agent_id, expires=expires)
            self._event(db, "task_heartbeat", agent_id=lease.agent_id, task_id=task.id)
            return True

    def complete(self, lease: TaskLease, result: WorkerResult) -> bool:
        with self._transaction() as db:
            task = self._owns(db, lease)
            if task is None:
                return False
            definition = SwarmRun.model_validate_json(self._run(db)["definition"])
            task.lease_expires_at = None
            task.worker_iterations += result.iterations
            db.execute(
                "UPDATE run SET input_tokens=input_tokens+?, output_tokens=output_tokens+? WHERE id=1",
                (result.input_tokens, result.output_tokens),
            )
            if result.status == "completed":
                task.status = TaskStatus.completed
                task.summary = result.summary
                task.artifacts = result.artifact_paths
                task.completed_at = _iso(self.clock())
                kind = "task_completed"
            elif result.status == "cancelled":
                self._stop(db, RunStatus.cancelled, "Worker cancelled")
                return True
            elif task.attempt < definition.autonomy.max_attempts:
                task.status = TaskStatus.pending
                task.agent_id = next(t.agent_id for t in definition.tasks if t.id == task.id)
                task.error = redact_internal_paths(result.error) or f"Worker returned {result.status.value}"
                kind = "task_retry"
            else:
                task.status = TaskStatus.failed
                task.error = redact_internal_paths(result.error) or f"Worker returned {result.status.value}"
                task.completed_at = _iso(self.clock())
                kind = "task_failed"
            self._save(db, task)
            self._event(
                db,
                kind,
                agent_id=lease.agent_id,
                task_id=task.id,
                data={
                    "status": result.status.value,
                    "error": task.error,
                    "iterations": result.iterations,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
            )
            self._advance(db)
            return True

    def cancel(self) -> None:
        with self._transaction() as db:
            if self._run(db)["status"] == "running":
                self._stop(db, RunStatus.cancelled, "Cancelled by user")

    def fail(self, reason: str) -> None:
        with self._transaction() as db:
            if self._run(db)["status"] == "running":
                self._event(db, "run_error", data={"error": redact_internal_paths(reason)})
                self._stop(db, RunStatus.failed, redact_internal_paths(reason))

    def append_event(self, event: SwarmEvent, lease: TaskLease | None = None) -> None:
        with self._transaction() as db:
            if lease is not None:
                if self._owns(db, lease) is None:
                    return
            elif self._run(db)["status"] != "running":
                return
            db.execute("INSERT INTO events(event) VALUES(?)", (event.model_dump_json(),))

    def set_grounding(self, data) -> None:
        with self._transaction() as db:
            db.execute("UPDATE run SET grounding=? WHERE id=1 AND grounding='null'", (json.dumps(data),))

    def snapshot(self, run: SwarmRun) -> SwarmRun:
        with self._transaction() as db:
            self._advance(db)
            state = self._run(db)
            tasks = self._tasks(db)
            final = next(
                (t.summary for t in tasks if t.id == run.final_task_id and t.status == TaskStatus.completed), None
            )
            return run.model_copy(
                update={
                    "tasks": tasks,
                    "status": RunStatus(state["status"]),
                    "completed_at": state["completed_at"],
                    "final_report": final,
                    "total_input_tokens": state["input_tokens"],
                    "total_output_tokens": state["output_tokens"],
                    "grounding_data": json.loads(state["grounding"]),
                }
            )

    def read_events(self, after_index: int = 0) -> list[SwarmEvent]:
        with self._transaction() as db:
            return [
                SwarmEvent.model_validate_json(row[0])
                for row in db.execute("SELECT event FROM events WHERE seq>? ORDER BY seq", (max(0, after_index),))
            ]

    def last_activity(self) -> str | None:
        with self._transaction() as db:
            row = db.execute("SELECT event FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            return SwarmEvent.model_validate_json(row[0]).timestamp if row else None
