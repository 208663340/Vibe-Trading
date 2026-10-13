"""Thread-safe run stop requests, progress and the main owner's terminal claim."""
from __future__ import annotations

import threading
import time
from typing import Callable

from src.agent.run_outcome import RunOutcome


class RunLifecycle:
    """One generation of a facade run; done means its owner finished cleanup."""

    def __init__(self, generation: int, cancel: threading.Event | None = None):
        self.generation = generation
        self.cancel = cancel if cancel is not None else threading.Event()
        self.done = threading.Event()
        self._gate = threading.RLock()
        self._progress_at = time.monotonic()
        self._stall_reason: str | None = None
        self._terminal: RunOutcome | None = None

    @property
    def stall_reason(self) -> str | None:
        with self._gate:
            return self._stall_reason

    def should_stop(self) -> bool:
        with self._gate:
            return self.cancel.is_set() or self.done.is_set() or self._terminal is not None

    def request_stop(self, reason: str | None = None) -> bool:
        with self._gate:
            if self.done.is_set() or self._terminal is not None:
                return False
            if reason is not None and self._stall_reason is None:
                self._stall_reason = reason
            self.cancel.set()
            return True

    def progress(self) -> None:
        with self._gate:
            if not self.should_stop():
                self._progress_at = time.monotonic()

    def idle_seconds(self) -> float:
        with self._gate:
            return max(0.0, time.monotonic() - self._progress_at)

    def emit(self, callback: Callable[[], None]) -> None:
        # Admission linearizes with terminal/finish; external callbacks run
        # without the ownership lock so a hung emitter cannot block Stop or
        # cleanup. An already admitted callback cannot be withdrawn.
        with self._gate:
            if self.done.is_set() or self._terminal is not None:
                return
        callback()

    def claim_terminal(self, candidate: RunOutcome) -> RunOutcome:
        with self._gate:
            if self._terminal is not None:
                raise RuntimeError("run terminal already claimed")
            if self._stall_reason is not None:
                candidate = RunOutcome("failed", self._stall_reason, "failure", False)
            elif self.cancel.is_set():
                candidate = RunOutcome("cancelled", "cancelled by user", "cancelled", False)
            self._terminal = candidate
            return candidate

    def finish(self) -> None:
        with self._gate:
            self.done.set()
