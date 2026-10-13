"""Loop-owned SSE subscriptions with bounded replay and gap/close controls."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid

from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional


@dataclass
class SSEEvent:
    """Server-sent event.

    Attributes:
        event_id: Globally unique event ID used for last_event_id recovery.
        event_type: Event type stored in the SSE ``event`` field.
        data: Event payload.
        session_id: Owning session ID.
        timestamp: Event timestamp.
    """

    event_id: Optional[str] = field(default_factory=lambda: uuid.uuid4().hex[:16])
    event_type: str = "message"
    data: Dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        """Format the event as an SSE text frame.

        Returns:
            Text that conforms to the SSE specification.
        """
        payload = json.dumps(self.data, ensure_ascii=False)
        lines = []
        if self.event_id:
            lines.append(f"id: {self.event_id}")
        lines.extend(
            [
                f"event: {self.event_type}",
                f"data: {payload}",
                "",
                "",
            ]
        )
        return "\n".join(lines)


logger = logging.getLogger(__name__)
_TERMINAL_EVENTS = frozenset({"attempt.completed", "attempt.failed", "attempt.cancelled"})


@dataclass(eq=False)
class _Subscriber:
    """Mutable transport state belongs to this subscriber's running loop."""

    loop: asyncio.AbstractEventLoop
    session_id: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    resync: SSEEvent | None = None
    terminal: SSEEvent | None = None
    closing: SSEEvent | None = None
    gapped: bool = False
    detached: bool = False
    ingress: deque = field(default_factory=deque)
    _ingress_gate: threading.Lock = field(default_factory=threading.Lock)
    _scheduled: bool = False
    _ingress_gapped: bool = False
    _ingress_gap: tuple[str, str | None] | None = None
    _ingress_terminal: SSEEvent | None = None
    _ingress_close: SSEEvent | None = None
    _latest_cursor: str | None = None

    def _pending_locked(self) -> bool:
        return bool(self.ingress or self._ingress_gap or self._ingress_terminal or self._ingress_close)

    def offer(self, event: SSEEvent, cursor: str | None) -> bool:
        """Bound publisher ingress; return whether one owner-loop drain is needed."""
        with self._ingress_gate:
            if self.detached or self._ingress_close is not None:
                return False
            if event.event_type in {"session_cleared", "_stream_closed"}:
                self._ingress_close = event
            else:
                self._latest_cursor = cursor
                if self._ingress_gapped:
                    if event.event_type in _TERMINAL_EVENTS:
                        if self._ingress_terminal is not None:
                            self._ingress_gap = ("queue_overflow", cursor)
                        self._ingress_terminal = event
                elif len(self.ingress) < self.queue.maxsize:
                    self.ingress.append((event, cursor))
                else:
                    self._ingress_gapped = True
                    self._ingress_gap = ("queue_overflow", cursor)
                    for queued, _ in self.ingress:
                        if queued.event_type in _TERMINAL_EVENTS:
                            self._ingress_terminal = queued
                    self.ingress.clear()
                    if event.event_type in _TERMINAL_EVENTS:
                        self._ingress_terminal = event
            if self._scheduled or not self._pending_locked():
                return False
            self._scheduled = True
            return True

    def drain(self) -> None:
        """Transfer one bounded ingress batch on the actual subscriber loop."""
        with self._ingress_gate:
            if self.detached:
                self._scheduled = False
                return
            events = list(self.ingress)
            self.ingress.clear()
            gap, terminal, close = self._ingress_gap, self._ingress_terminal, self._ingress_close
            self._ingress_gap = self._ingress_terminal = self._ingress_close = None
        try:
            if gap is not None:
                self.gap(*gap)
            for event, cursor in events:
                self.deliver(event, cursor)
            if terminal is not None:
                self.deliver(terminal, terminal.event_id)
            if close is not None:
                self.deliver(close, None)
        finally:
            self._finish_drain()

    def _finish_drain(self) -> None:
        # Reset/schedule and publisher admission share a lock. A publisher
        # racing this handoff either joins the next drain or schedules it itself.
        with self._ingress_gate:
            if self._pending_locked() and not self.detached:
                self.loop.call_soon(self.drain)
            else:
                self._scheduled = False

    def detach(self) -> None:
        with self._ingress_gate:
            self.detached = True
            self.ingress.clear()
            self._ingress_gap = self._ingress_terminal = self._ingress_close = None

    def gap(self, reason: str, cursor: str | None) -> None:
        self.gapped = True
        with self._ingress_gate:
            self._ingress_gapped = True
            if reason == "cursor_expired":
                self._latest_cursor = cursor
        while not self.queue.empty():
            queued = self.queue.get_nowait()
            if queued.event_type in _TERMINAL_EVENTS:
                self.terminal = queued
        if self.resync is None:
            self.resync = SSEEvent(
                event_id=None,
                event_type="session.resync",
                data={"reason": reason, "latest_event_id": cursor},
                session_id=self.session_id,
            )
        else:
            self.resync.data["latest_event_id"] = cursor
        self.wake.set()

    def deliver(self, event: SSEEvent, cursor: str | None) -> None:
        if self.detached or self.closing is not None:
            return
        if event.event_type in {"session_cleared", "_stream_closed"}:
            self.closing = event
            self.wake.set()
            return
        if self.gapped:
            if self.resync is not None:
                self.resync.data["latest_event_id"] = cursor
            if event.event_type in _TERMINAL_EVENTS:
                if self.terminal is not None:
                    # Bounded coalescing requires REST recovery of replaced terminals.
                    self.gap("queue_overflow", cursor)
                self.terminal = event
                self.wake.set()
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.gap("queue_overflow", cursor)
            if event.event_type in _TERMINAL_EVENTS:
                self.terminal = event
        self.wake.set()

    def take(self) -> SSEEvent | None:
        if self.resync is not None:
            with self._ingress_gate:
                self.resync.data["latest_event_id"] = self._latest_cursor
        for slot in ("resync", "terminal", "closing"):
            event = getattr(self, slot)
            if (
                slot == "closing"
                and event is not None
                and event.event_type == "_stream_closed"
                and not self.queue.empty()
            ):
                # Runtime shutdown drains its bounded queue so a queued durable
                # terminal is delivered before transport EOF. Session deletion
                # remains an immediate session_cleared control.
                return self.queue.get_nowait()
            if event is not None:
                setattr(self, slot, None)
                return event
        if not self.queue.empty():
            return self.queue.get_nowait()
        return None


class EventBus:
    """Bounded session replay with per-subscriber loop and gap controls."""

    def __init__(self, max_buffer_size: int = 500, heartbeat_interval_s: float = 30.0) -> None:
        self.max_buffer_size = max_buffer_size
        self.heartbeat_interval_s = heartbeat_interval_s
        self._buffers: Dict[str, List[SSEEvent]] = {}
        self._subscribers: Dict[str, List[_Subscriber]] = {}
        self._listeners: List[Callable[[SSEEvent], None]] = []
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._closed = False

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Legacy seam; subscriptions always capture their own running loop."""
        self._loop = loop

    def _schedule_locked(self, sub: _Subscriber, event: SSEEvent, cursor: str | None) -> None:
        # The bus gate orders ingress with replay registration. The mailbox
        # gate coalesces wakeups; callback handles never hold each event payload.
        if not sub.offer(event, cursor):
            return
        try:
            sub.loop.call_soon_threadsafe(sub.drain)
        except RuntimeError:
            sub.detach()  # Closed loop: no surviving transport.
            subscribers = self._subscribers.get(sub.session_id, [])
            if sub in subscribers:
                subscribers.remove(sub)

    def publish(self, event: SSEEvent) -> None:
        with self._lock:
            if self._closed:
                return
            buffer = self._buffers.setdefault(event.session_id, [])
            buffer.append(event)
            self._buffers[event.session_id] = buffer[-self.max_buffer_size :]
            for sub in list(self._subscribers.get(event.session_id, [])):
                self._schedule_locked(sub, event, event.event_id)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                logger.exception("EventBus listener failed for %s", event.event_type)

    def add_listener(self, listener: Callable[[SSEEvent], None]) -> None:
        with self._lock:
            if not self._closed and listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[SSEEvent], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def emit(self, session_id: str, event_type: str, data: Optional[Dict[str, Any]] = None) -> SSEEvent:
        event = SSEEvent(event_type=event_type, data=data or {}, session_id=session_id)
        self.publish(event)
        return event

    def replay(
        self, session_id: str, last_event_id: Optional[str] = None, *, replay_all: bool = False
    ) -> List[SSEEvent]:
        with self._lock:
            return self._replay_locked(session_id, last_event_id, replay_all=replay_all)

    def _replay_locked(
        self, session_id: str, last_event_id: Optional[str] = None, *, replay_all: bool = False
    ) -> List[SSEEvent]:
        buffer = self._buffers.get(session_id, [])
        if not last_event_id:
            return list(buffer) if replay_all else []
        for index, event in enumerate(buffer):
            if event.event_id == last_event_id:
                return list(buffer[index + 1 :])
        # Legacy snapshot helper; subscriptions explicitly signal missing cursors.
        return list(buffer) if replay_all else []

    async def subscribe(
        self, session_id: str, last_event_id: Optional[str] = None, *, replay_all: bool = False
    ) -> AsyncIterator[SSEEvent]:
        sub = _Subscriber(asyncio.get_running_loop(), session_id)
        with self._lock:
            buffer = self._buffers.get(session_id, [])
            replay_events = self._replay_locked(session_id, last_event_id, replay_all=replay_all)
            expired = last_event_id and not any(event.event_id == last_event_id for event in buffer)
            if self._closed:
                sub.closing = SSEEvent(event_id=None, event_type="_stream_closed", session_id=session_id)
                replay_events = []
            else:
                self._subscribers.setdefault(session_id, []).append(sub)
                if expired:
                    sub.gap("cursor_expired", buffer[-1].event_id if buffer else None)
                    replay_events = []
        try:
            for event in replay_events:
                if sub.gapped or sub.closing is not None:
                    break
                yield event
            while True:
                event = sub.take()
                if event is None:
                    sub.wake.clear()
                    try:
                        await asyncio.wait_for(sub.wake.wait(), self.heartbeat_interval_s)
                    except asyncio.TimeoutError:
                        yield SSEEvent(
                            event_id=None, event_type="heartbeat", data={"ts": time.time()}, session_id=session_id
                        )
                    continue
                if event.event_type == "_stream_closed":
                    break  # App shutdown is transport closure, never session deletion.
                yield event
                if event.event_type == "session_cleared":
                    break
        finally:
            sub.detach()
            with self._lock:
                subscribers = self._subscribers.get(session_id, [])
                if sub in subscribers:
                    subscribers.remove(sub)
                if not subscribers:
                    self._subscribers.pop(session_id, None)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._buffers.pop(session_id, None)
            sentinel = SSEEvent(event_id=None, event_type="session_cleared", session_id=session_id)
            for sub in self._subscribers.pop(session_id, []):
                self._schedule_locked(sub, sentinel, None)

    def close(self) -> None:
        """Stop admission and wake transports even when their data queues are full."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._buffers.clear()
            self._listeners.clear()
            subscribers = [sub for group in self._subscribers.values() for sub in group]
            self._subscribers.clear()
            for sub in subscribers:
                sentinel = SSEEvent(event_id=None, event_type="_stream_closed", session_id=sub.session_id)
                self._schedule_locked(sub, sentinel, None)
