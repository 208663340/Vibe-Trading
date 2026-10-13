"""Bounded tool admission, fenced reader waits and ordered batch commits.

The caller observes results and emits reader events. Reader producers retain
leases until they exit, even after the waiting caller has revoked delivery.
Running writes retain caller ownership and their existing heartbeat/warning timers.
"""

from __future__ import annotations

import contextvars
import copy
import json
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from src.agent.progress import HeartbeatTimer, ProgressEvent, _set_emitter


@dataclass(frozen=True)
class PreparedToolCall:
    index: int
    call_id: str
    name: str
    arguments: dict[str, Any]
    readonly: bool
    call: Any = None
    decision: Any = None


@dataclass(frozen=True)
class ToolExecutionResult:
    prepared: PreparedToolCall
    raw_result: str
    elapsed_ms: int
    executed: bool
    outcome: str
    exception: BaseException | None = None

    @property
    def index(self) -> int:
        return self.prepared.index

    @property
    def call_id(self) -> str:
        return self.prepared.call_id


class ReadonlyToolBudget:
    """Process resource ownership; caller revocation does not free capacity."""

    def __init__(self, capacity: int = 32):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._gate = threading.Lock()
        self._leases: set[_Reader] = set()

    def acquire(self, reader: _Reader) -> bool:
        with self._gate:
            if len(self._leases) >= self.capacity:
                return False
            self._leases.add(reader)
            return True

    def release(self, reader: _Reader) -> None:
        with self._gate:
            self._leases.discard(reader)

    @property
    def active_count(self) -> int:
        with self._gate:
            return len(self._leases)

    @property
    def abandoned_count(self) -> int:
        with self._gate:
            return sum(reader.revoked.is_set() for reader in self._leases)


_PROCESS_READONLY_BUDGET = ReadonlyToolBudget()


def abandoned_readonly_tool_count() -> int:
    """Read-only count of revoked process-default producers still alive."""
    return _PROCESS_READONLY_BUDGET.abandoned_count


@dataclass(eq=False)
class _Reader:
    prepared: PreparedToolCall
    started_at: float
    revoked: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    gate: threading.Lock = field(default_factory=threading.Lock)
    events: deque = field(default_factory=lambda: deque(maxlen=32))
    raw_result: str = ""
    exception: BaseException | None = None
    executed: bool = False
    next_heartbeat: float = 0

    def publish(self, event: ProgressEvent) -> None:
        with self.gate:
            if not self.revoked.is_set():
                self.events.append(event)


class ToolInvoker:
    """Invoke writes under caller ownership and start bounded readonly workers."""

    def __init__(
        self,
        execute: Callable[[str, dict[str, Any]], str],
        *,
        should_cancel: Callable[[], bool],
        emit: Callable[[str, dict[str, Any]], None],
        timeout_s: float | None = None,
        heartbeat_interval_s: float = 3,
        budget: ReadonlyToolBudget | None = None,
        before_write: Callable[[], None] | None = None,
        heartbeat_factory: Callable[..., Any] = HeartbeatTimer,
    ):
        self.execute = execute
        self.should_cancel = should_cancel
        self.emit = emit
        self.timeout_s = timeout_s if timeout_s and timeout_s > 0 else None
        self.heartbeat_interval_s = max(0.5, heartbeat_interval_s)
        self.budget = budget if budget is not None else _PROCESS_READONLY_BUDGET
        self.before_write = before_write
        self.heartbeat_factory = heartbeat_factory

    def diagnostic(
        self,
        prepared: PreparedToolCall,
        code: str,
        *,
        elapsed_ms: int = 0,
        executed: bool = False,
        exception: BaseException | None = None,
    ) -> ToolExecutionResult:
        messages = {"tool_cancelled": "Tool wait cancelled", "tool_capacity": "Readonly tool capacity exhausted"}
        message = messages.get(code, str(exception) if exception else "Tool execution failed")
        payload = {"status": "error", "error_code": code, "tool": prepared.name, "message": message}
        if code == "tool_timeout":
            label = f"{self.timeout_s:g}s"
            payload.update(timeout_seconds=self.timeout_s, message=f"Tool exceeded {label} timeout")
        return ToolExecutionResult(
            prepared, json.dumps(payload, ensure_ascii=False), elapsed_ms, executed, code, exception
        )

    def start(self, prepared: PreparedToolCall) -> _Reader | ToolExecutionResult:
        if self.should_cancel():
            return self.diagnostic(prepared, "tool_cancelled")
        reader = _Reader(prepared, time.monotonic())
        reader.next_heartbeat = reader.started_at + self.heartbeat_interval_s
        if not self.budget.acquire(reader):
            return self.diagnostic(prepared, "tool_capacity")
        try:
            # Independent args and context are captured before handing ownership
            # to the worker. It only publishes raw results/bounded progress data.
            args = copy.deepcopy(prepared.arguments)
            ctx = contextvars.copy_context()

            def work():
                _set_emitter(reader.publish)
                try:
                    if not reader.revoked.is_set():
                        reader.executed = True
                        reader.raw_result = self.execute(prepared.name, args) or ""
                except BaseException as exc:  # noqa: BLE001 - transported to caller
                    reader.exception = exc
                finally:
                    _set_emitter(None)
                    self.budget.release(reader)
                    reader.done.set()

            threading.Thread(target=lambda: ctx.run(work), name=f"tool-{prepared.name}", daemon=True).start()
        except BaseException:
            self.budget.release(reader)
            raise
        return reader

    def poll(self, reader: _Reader) -> ToolExecutionResult | None:
        elapsed_ms = int((time.monotonic() - reader.started_at) * 1000)
        # Cancellation fences even a completed-but-unconsumed result.
        if self.should_cancel():
            with reader.gate:
                reader.revoked.set()
                reader.events.clear()
            return self.diagnostic(reader.prepared, "tool_cancelled", elapsed_ms=elapsed_ms, executed=reader.executed)
        if self.timeout_s is not None and elapsed_ms >= self.timeout_s * 1000 and not reader.done.is_set():
            with reader.gate:
                reader.revoked.set()
                reader.events.clear()
            result = self.diagnostic(reader.prepared, "tool_timeout", elapsed_ms=elapsed_ms, executed=reader.executed)
            self.emit(
                "tool_progress",
                {
                    "tool": reader.prepared.name,
                    "call_id": reader.prepared.call_id,
                    "stage": "timeout",
                    "message": json.loads(result.raw_result)["message"],
                    "elapsed_s": round(elapsed_ms / 1000, 2),
                },
            )
            return result
        with reader.gate:
            events = list(reader.events)
            reader.events.clear()

        def deliver(events):
            for event in events:
                if self.should_cancel():
                    return False
                payload = event.to_dict()
                payload.update(tool=reader.prepared.name, call_id=reader.prepared.call_id)
                self.emit("tool_progress", payload)
            return True

        if not deliver(events) or self.should_cancel():
            with reader.gate:
                reader.revoked.set()
                reader.events.clear()
            return self.diagnostic(reader.prepared, "tool_cancelled", elapsed_ms=elapsed_ms, executed=reader.executed)
        if reader.done.is_set():
            # A callback may have blocked while the worker finished. Drain its
            # bounded final progress before accepting completion, on the caller.
            with reader.gate:
                events = list(reader.events)
                reader.events.clear()
                reader.revoked.set()
            if not deliver(events) or self.should_cancel():
                return self.diagnostic(
                    reader.prepared, "tool_cancelled", elapsed_ms=elapsed_ms, executed=reader.executed
                )
            if reader.exception is not None:
                return self.diagnostic(
                    reader.prepared,
                    "tool_exception",
                    elapsed_ms=elapsed_ms,
                    executed=reader.executed,
                    exception=reader.exception,
                )
            return ToolExecutionResult(reader.prepared, reader.raw_result, elapsed_ms, reader.executed, "executed")
        now = time.monotonic()
        if now >= reader.next_heartbeat:
            reader.next_heartbeat = now + self.heartbeat_interval_s
            self.emit(
                "tool_heartbeat",
                {
                    "tool": reader.prepared.name,
                    "call_id": reader.prepared.call_id,
                    "elapsed_s": round(elapsed_ms / 1000, 2),
                },
            )
        return None

    def invoke(self, prepared: PreparedToolCall) -> ToolExecutionResult:
        if prepared.readonly:
            reader = self.start(prepared)
            if isinstance(reader, ToolExecutionResult):
                return reader
            try:
                while True:
                    result = self.poll(reader)
                    if result is not None:
                        return result
                    reader.done.wait(0.01)
            finally:
                with reader.gate:
                    reader.revoked.set()
                    reader.events.clear()
        if self.should_cancel():
            return self.diagnostic(prepared, "tool_cancelled")
        started_at = time.monotonic()
        finished = threading.Event()

        def progress(event):
            payload = event.to_dict()
            payload.update(tool=prepared.name, call_id=prepared.call_id)
            self.emit("tool_progress", payload)

        def heartbeat(payload):
            payload["call_id"] = prepared.call_id
            self.emit("tool_heartbeat", payload)

        def warn():
            if self.timeout_s is None or finished.wait(self.timeout_s):
                return
            self.emit(
                "tool_progress",
                {
                    "tool": prepared.name,
                    "call_id": prepared.call_id,
                    "stage": "timeout_warning",
                    "readonly": False,
                    "message": f"Write tool exceeded {self.timeout_s:g}s timeout; waiting for completion because it cannot be safely cancelled",
                    "elapsed_s": round(time.monotonic() - started_at, 2),
                },
            )

        if self.timeout_s is not None:
            threading.Thread(target=warn, name=f"tool-watchdog-{prepared.name}", daemon=True).start()
        _set_emitter(progress)
        executed = False
        try:
            with self.heartbeat_factory(prepared.name, self.heartbeat_interval_s, heartbeat):
                # This is the mutation linearization point, after stop admission
                # and immediately before execute; even a partial failure expires reads.
                if self.should_cancel():
                    return self.diagnostic(prepared, "tool_cancelled")
                if self.before_write is not None:
                    self.before_write()
                executed = True
                raw = self.execute(prepared.name, prepared.arguments) or ""
            return ToolExecutionResult(prepared, raw, int((time.monotonic() - started_at) * 1000), True, "executed")
        except Exception as exc:  # noqa: BLE001 - ordered error commit
            return self.diagnostic(
                prepared,
                "tool_exception",
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
                executed=executed,
                exception=exc,
            )
        finally:
            finished.set()
            _set_emitter(None)


class ToolBatchExecutor:
    """At most eight admitted readers; observe promptly and commit input order."""

    def __init__(self, invoker: ToolInvoker, *, capacity: int = 8):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.invoker = invoker
        self.capacity = capacity

    def execute(
        self,
        calls: list[PreparedToolCall],
        *,
        prepare: Callable[[PreparedToolCall], PreparedToolCall | ToolExecutionResult],
        commit: Callable[[ToolExecutionResult], None],
        observe: Callable[[ToolExecutionResult], None],
    ) -> None:
        active: dict[int, _Reader] = {}
        retired: list[_Reader] = []
        completed: dict[int, ToolExecutionResult] = {}
        next_start = next_commit = 0
        try:
            while next_commit < len(calls):
                # Revoked producers retain this batch's physical slot until
                # actual exit, independently of their settled waiting result.
                retired[:] = [reader for reader in retired if not reader.done.is_set()]
                # Observe all completed calls before admission/ordered commit.
                for index, reader in list(active.items()):
                    result = self.invoker.poll(reader)
                    if result is not None:
                        completed[index] = result
                        del active[index]
                        if not reader.done.is_set():
                            retired.append(reader)
                        observe(result)
                while next_commit in completed:
                    commit(completed.pop(next_commit))
                    next_commit += 1
                while next_start < len(calls) and len(active) < self.capacity:
                    call = calls[next_start]
                    # Writes/synthetic compaction boundaries wait for earlier commit.
                    if not call.readonly and next_commit != next_start:
                        break
                    if self.invoker.should_cancel():
                        result = self.invoker.diagnostic(call, "tool_cancelled")
                    else:
                        decision = prepare(call)
                        if isinstance(decision, ToolExecutionResult):
                            result = decision
                        elif call.readonly:
                            try:
                                reader = (
                                    self.invoker.diagnostic(decision, "tool_capacity")
                                    if len(active) + len(retired) >= self.capacity
                                    else self.invoker.start(decision)
                                )
                            except Exception as exc:  # noqa: BLE001 - ordered start failure
                                reader = self.invoker.diagnostic(decision, "tool_exception", exception=exc)
                            if not isinstance(reader, ToolExecutionResult):
                                active[next_start] = reader
                                next_start += 1
                                continue
                            result = reader
                        else:
                            result = self.invoker.invoke(decision)
                    completed[next_start] = result
                    observe(result)
                    next_start += 1
                    if not call.readonly:
                        break
                if active:
                    threading.Event().wait(0.005)
        finally:
            for reader in active.values():
                with reader.gate:
                    reader.revoked.set()
                    reader.events.clear()
