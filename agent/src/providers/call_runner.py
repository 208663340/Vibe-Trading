"""Bounded blocking provider producers with caller-owned result consumption.

Cancellation revokes result delivery, not Python threads. A revoked producer keeps
its process permit until its SDK call and iterator cleanup have actually exited.
"""

from __future__ import annotations

import contextvars
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
_POLL_SECONDS = 0.01


class ProviderCallCancelled(RuntimeError):
    """The caller stopped waiting; the producer may still be running."""


class ProviderCallCapacityError(RuntimeError):
    """All actual producer slots are occupied (there is no pending queue)."""


@dataclass(eq=False)
class _Lease:
    revoked: threading.Event = field(default_factory=threading.Event)


class ProviderCallBudget:
    """Finite producer ownership; injectable private budgets support tests."""

    def __init__(self, capacity: int = 8) -> None:
        if capacity < 1:
            raise ValueError("provider capacity must be positive")
        self.capacity = capacity
        self._gate = threading.Lock()
        self._leases: set[_Lease] = set()

    def acquire(self) -> _Lease:
        with self._gate:
            if len(self._leases) >= self.capacity:
                raise ProviderCallCapacityError("Provider producer capacity exhausted")
            lease = _Lease()
            self._leases.add(lease)
            return lease

    def release(self, lease: _Lease) -> None:
        with self._gate:
            self._leases.discard(lease)

    @property
    def active_count(self) -> int:
        with self._gate:
            return len(self._leases)

    @property
    def abandoned_count(self) -> int:
        with self._gate:
            return sum(lease.revoked.is_set() for lease in self._leases)


_PROCESS_PROVIDER_BUDGET = ProviderCallBudget(8)


def abandoned_provider_count() -> int:
    """Read-only process-wide count for API shutdown snapshots."""
    return _PROCESS_PROVIDER_BUDGET.abandoned_count


@dataclass(frozen=True)
class _Scope:
    should_cancel: Callable[[], bool] | None = None
    deadline: float | None = None


_SCOPE: contextvars.ContextVar[_Scope] = contextvars.ContextVar("provider_call_scope", default=_Scope())


@contextmanager
def provider_call_scope(
    *, should_cancel: Callable[[], bool] | None = None, deadline: float | None = None
) -> Iterator[None]:
    """Bind run cancellation/absolute monotonic deadline without changing chat API."""
    parent = _SCOPE.get()
    predicates = [p for p in (parent.should_cancel, should_cancel) if p is not None]
    deadlines = [d for d in (parent.deadline, deadline) if d is not None]
    token = _SCOPE.set(
        _Scope(
            (lambda: any(p() for p in predicates)) if predicates else None,
            min(deadlines) if deadlines else None,
        )
    )
    try:
        yield
    finally:
        _SCOPE.reset(token)


_PRODUCER_LEASE: contextvars.ContextVar[_Lease | None] = contextvars.ContextVar("provider_producer_lease", default=None)


def raise_if_provider_revoked() -> None:
    """Fence a second SDK operation inside the same logical producer."""
    lease = _PRODUCER_LEASE.get()
    if lease is not None and lease.revoked.is_set():
        raise ProviderCallCancelled("Provider call cancelled")


class ProviderCallRunner:
    """Start at most one bounded producer for each logical provider attempt."""

    def __init__(
        self,
        *,
        budget: ProviderCallBudget | None = None,
        queue_capacity: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("provider queue capacity must be positive")
        self.budget = budget if budget is not None else _PROCESS_PROVIDER_BUDGET
        self._queue_capacity = queue_capacity
        self._clock = clock
        self._condition = threading.Condition()
        self._active: set[_Lease] = set()
        self._finishing: set[_Lease] = set()
        self._closing = False
        self._on_idle: Callable[[], None] | None = None

    def defer_close(self, callback: Callable[[], None]) -> bool:
        """Fence admission/delivery; return whether an active producer owns close."""
        with self._condition:
            if self._closing:
                return True
            self._closing = True
            for lease in self._active:
                lease.revoked.set()
            if self._active:
                self._on_idle = callback
                return True
        return False

    def close_when_idle(self, callback: Callable[[], None]) -> None:
        """Fence this owner now and defer owned client close until actual exit."""
        if not self.defer_close(callback):
            callback()

    def wait_for_idle(self, timeout: float) -> bool:
        """Observe producer cleanup completion; principally for controlled teardown."""
        with self._condition:
            return self._condition.wait_for(lambda: not self._active, timeout)

    def run_call(
        self,
        call: Callable[[], Any],
        *,
        timeout_s: float | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> Any:
        def produce():
            yield call()

        deadline = self._clock() + timeout_s if timeout_s and timeout_s > 0 else None
        values = self.stream(produce, should_cancel=should_cancel, deadline=deadline)
        result = next(values)
        # Observe cleanup/terminal before releasing a synchronous call result.
        try:
            next(values)
        except StopIteration:
            return result
        raise RuntimeError("single provider call produced multiple results")

    def stream(
        self,
        produce: Callable[[], Iterator[Any]],
        *,
        should_cancel: Callable[[], bool] | None = None,
        idle_timeout_s: float | None = None,
        deadline: float | None = None,
    ) -> Iterator[Any]:
        scope = _SCOPE.get()
        predicates = [p for p in (scope.should_cancel, should_cancel) if p is not None]
        deadlines = [d for d in (scope.deadline, deadline) if d is not None]
        deadline = min(deadlines) if deadlines else None
        if any(p() for p in predicates):
            raise ProviderCallCancelled("Provider call cancelled")
        if deadline is not None and self._clock() >= deadline:
            raise TimeoutError("Provider call deadline exceeded")
        with self._condition:
            if self._closing:
                raise ProviderCallCancelled("Provider owner is closing")
            lease = self.budget.acquire()
            self._active.add(lease)
        payloads: queue.Queue[Any] = queue.Queue(self._queue_capacity)
        terminal = threading.Event()  # Never competes with payload slots.
        errors: list[BaseException] = []
        last_arrival = [self._clock()]
        delivering = threading.Event()
        context = contextvars.copy_context()

        def release_locked() -> None:
            self.budget.release(lease)
            self._active.discard(lease)
            self._finishing.discard(lease)
            terminal.set()
            self._condition.notify_all()

        def finish() -> None:
            with self._condition:
                self._finishing.add(lease)
                callback = self._on_idle if self._active == self._finishing else None
                if callback is None:
                    # Select cleanup versus exit atomically with close admission.
                    release_locked()
                    return
                self._on_idle = None
            # Client close can itself block: keep ownership, but never
            # hold the observation lock while calling external cleanup.
            try:
                callback()
            except BaseException:
                logger.debug("Deferred provider close failed", exc_info=True)
            finally:
                with self._condition:
                    release_locked()

        def worker() -> None:
            iterator = None
            _PRODUCER_LEASE.set(lease)
            try:
                iterator = iter(produce())
                while not lease.revoked.is_set():
                    try:
                        value = next(iterator)
                    except StopIteration:
                        break
                    delivering.set()
                    last_arrival[0] = self._clock()
                    while not lease.revoked.is_set():
                        try:
                            payloads.put(value, timeout=_POLL_SECONDS)
                            # Backpressure belongs to the caller, not provider idle.
                            last_arrival[0] = self._clock()
                            delivering.clear()
                            break
                        except queue.Full:
                            continue
            except BaseException as exc:
                errors.append(exc)
            finally:
                try:
                    close = getattr(iterator, "close", None)
                    if callable(close):
                        close()
                except BaseException as exc:
                    if not errors:
                        errors.append(exc)
                    else:
                        logger.debug("Provider iterator cleanup failed", exc_info=True)
                finally:
                    finish()

        try:
            thread = threading.Thread(target=context.run, args=(worker,), name="provider-call", daemon=True)
            thread.start()
        except BaseException:
            finish()
            raise
        try:
            while True:
                if self._closing or lease.revoked.is_set() or any(p() for p in predicates):
                    raise ProviderCallCancelled("Provider call cancelled")
                if deadline is not None and self._clock() >= deadline:
                    raise TimeoutError("Provider call deadline exceeded")
                try:
                    value = payloads.get_nowait()
                except queue.Empty:
                    if terminal.is_set():
                        if errors:
                            raise errors[0]
                        return
                    if (
                        not delivering.is_set()
                        and idle_timeout_s
                        and idle_timeout_s > 0
                        and self._clock() - last_arrival[0] >= idle_timeout_s
                    ):
                        raise TimeoutError(f"no stream delta for {idle_timeout_s:g}s")
                    terminal.wait(_POLL_SECONDS)
                    continue
                # Cancellation has priority even when a result arrived concurrently.
                if self._closing or lease.revoked.is_set() or any(p() for p in predicates):
                    raise ProviderCallCancelled("Provider call cancelled")
                yield value
        finally:
            if not terminal.is_set():
                lease.revoked.set()
