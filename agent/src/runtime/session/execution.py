"""Identity-based ownership of session executions and their actual workers."""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import threading
import weakref
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class SessionBusyError(RuntimeError):
    """A session has an execution or finalizer which still owns its writes."""


class SessionServiceClosingError(RuntimeError):
    """Admission is closed, or another service still owns this store."""


class PreStartCancelled(Exception):
    """Cancellation was admitted before the main worker started."""


class ExecutionState(str, Enum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass(frozen=True)
class CloseSummary:
    complete: bool
    pending_run_writers: tuple[str, ...] = ()
    pending_finalizers: tuple[str, ...] = ()
    abandoned_readers: int = 0
    abandoned_producers: int = 0


@dataclass(eq=False)
class ExecutionHandle:
    session_id: str
    attempt_id: str
    task: asyncio.Task | None = None
    execution: asyncio.Task | None = None
    finalizer: asyncio.Task | None = None
    workers: set[concurrent.futures.Future] = field(default_factory=set)
    finalization_future: concurrent.futures.Future | None = None
    phase: str = "reserved"
    user_cancel: bool = False
    interrupted: bool = False
    main_started: bool = False
    finalization_done: bool = False
    persistence_error: BaseException | None = None
    started_at: float = 0.0


# One in-process storage owner; the gate also linearizes retiring an idle owner
# against new admission on that owner. Initializing/recovering owners and live
# workers keep a strong registration until their actual write paths exit.
_OWNER_GATE = threading.RLock()
_OWNERS: weakref.WeakValueDictionary[Path, ExecutionSupervisor] = weakref.WeakValueDictionary()
_LIVE_OWNERS: dict[Path, ExecutionSupervisor] = {}


class ExecutionSupervisor:
    def __init__(self, store_dir: Path, executor: concurrent.futures.Executor, max_inflight: int = 16):
        if max_inflight < 1:
            raise ValueError("max_inflight must be positive")
        self.gate = _OWNER_GATE
        self.store_dir = store_dir.resolve()
        self.executor = executor
        self.max_inflight = max_inflight
        self.state = ExecutionState.OPEN
        self.initializing = True
        self.handles: dict[str, ExecutionHandle] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.inflight: set[str] = set()
        with self.gate:
            previous = _OWNERS.get(self.store_dir)
            if previous is not None:
                if previous.initializing or previous.handles:
                    raise SessionServiceClosingError("An active execution owner still owns this session store")
                previous.state = ExecutionState.CLOSED
            _OWNERS[self.store_dir] = self
            _LIVE_OWNERS[self.store_dir] = self

    def finish_initialization(self, *, failed: bool = False) -> None:
        """Release startup ownership only after recovery/failure cleanup exits."""
        with self.gate:
            self.initializing = False
            if failed or self.state == ExecutionState.CLOSING:
                self.state = ExecutionState.CLOSED
            if not self.handles:
                _LIVE_OWNERS.pop(self.store_dir, None)

    def require_open(self) -> None:
        if self.initializing or self.state != ExecutionState.OPEN:
            raise SessionServiceClosingError("Session service is closing or closed")

    def reserve(self, session_id: str, attempt_id: str) -> ExecutionHandle:
        with self.gate:
            self.require_open()
            if session_id in self.handles:
                raise SessionBusyError(f"Session {session_id} already has a run in progress")
            if len(self.handles) >= self.max_inflight:
                raise SessionServiceClosingError("Session execution admission capacity is exhausted")
            handle = ExecutionHandle(session_id, attempt_id)
            self.handles[session_id] = handle
            self.inflight.add(session_id)
            _LIVE_OWNERS[self.store_dir] = self
            return handle

    def owns(self, handle: ExecutionHandle) -> bool:
        with self.gate:
            return self.handles.get(handle.session_id) is handle

    def release(self, handle: ExecutionHandle) -> bool:
        """An old completion can never release a newer attempt's claim."""
        with self.gate:
            if not self.owns(handle) or not handle.finalization_done:
                return False
            if any(not worker.done() for worker in handle.workers):
                return False
            if handle.finalization_future is not None and not handle.finalization_future.done():
                return False
            if handle.execution is not None and not handle.execution.done():
                return False
            self.handles.pop(handle.session_id)
            self.inflight.discard(handle.session_id)
            if self.tasks.get(handle.session_id) is handle.task:
                self.tasks.pop(handle.session_id, None)
            handle.phase = "released"
            if not self.handles and not self.initializing:
                _LIVE_OWNERS.pop(self.store_dir, None)
                if self.state == ExecutionState.CLOSING:
                    self.state = ExecutionState.CLOSED
            return True

    def submit(self, handle: ExecutionHandle | None, fn: Callable[[], Any], *, main: bool = False, finalizer: bool = False) -> concurrent.futures.Future:
        context = contextvars.copy_context()
        def execute():
            if handle is not None:
                with self.gate:
                    if not self.owns(handle) or (not finalizer and (handle.user_cancel or handle.interrupted) and not handle.main_started):
                        raise PreStartCancelled()
                    if main:
                        handle.main_started = True
                        handle.phase = "running"
            return context.run(fn)
        with self.gate:
            future = self.executor.submit(execute)
            if handle is not None:
                if finalizer:
                    handle.finalization_future = future
                    handle.phase = "finalizing"
                else:
                    handle.workers.add(future)
                    handle.phase = "submitted" if not handle.main_started else "running"
            return future

    def begin_close(self) -> tuple[ExecutionHandle, ...]:
        with self.gate:
            if self.state == ExecutionState.OPEN:
                self.state = ExecutionState.CLOSING
            if not self.handles and not self.initializing:
                self.state = ExecutionState.CLOSED
            return tuple(self.handles.values())

    def execution_loops(self) -> frozenset[asyncio.AbstractEventLoop | None]:
        """Read ownership of every unreleased handle; None means unproven loop."""
        with self.gate:
            loops = set()
            for handle in self.handles.values():
                tasks = [task for task in (handle.task, handle.execution, handle.finalizer) if task is not None]
                loops.update(task.get_loop() for task in tasks)
                if not tasks:
                    loops.add(None)
            return frozenset(loops)

    def close_summary(self) -> CloseSummary:
        with self.gate:
            runs, finalizers = [], []
            for handle in self.handles.values():
                if (any(not worker.done() for worker in handle.workers)
                    or (handle.execution is not None and not handle.execution.done())
                    or handle.phase in {"reserved", "submitted", "running"}):
                    runs.append(handle.attempt_id)
                else:
                    finalizers.append(handle.attempt_id)
            return CloseSummary(not self.handles and not self.initializing, tuple(runs), tuple(finalizers))
