"""Application-owned session execution resources and monotonic close ordering."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from src.providers.call_runner import abandoned_provider_count
from src.session.events import EventBus
from src.session.execution import CloseSummary, SessionServiceClosingError
from src.session.service import SessionService
from src.session.store import SessionStore


class RuntimeServices:
    """Retain writer resources until the service reports actual completion.

    The API boundary owns legacy globals. This container never imports the API
    host or awaits/cancels old-loop tasks to manufacture a complete close.
    """

    def __init__(self, sessions_dir: Path, runs_dir: Path) -> None:
        self.sessions_dir = Path(sessions_dir).resolve()
        self.runs_dir = Path(runs_dir)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="session-runtime")
        self.session_service: SessionService | None = None
        self.closing = False
        self.closed = False
        self.close_summary = CloseSummary(True)
        self._gate = threading.RLock()

    def get_session_service(self) -> SessionService:
        with self._gate:
            if self.closing:
                raise SessionServiceClosingError("Application session runtime is closing")
            if self.session_service is None:
                bus = EventBus()
                try:
                    self.session_service = SessionService(
                        SessionStore(self.sessions_dir),
                        bus,
                        self.runs_dir,
                        executor=self.executor,
                    )
                except BaseException:
                    bus.close()
                    raise
            return self.session_service

    def retire_idle(self) -> bool:
        """Respect legacy root overrides without discarding an execution owner."""
        with self._gate:
            if self.session_service is not None:
                supervisor = self.session_service._supervisor
                with supervisor.gate:
                    if not supervisor.close_summary().complete:
                        return False
                    supervisor.begin_close()
            self.closing = True
            self._release_resources()
            return True

    def _provider_snapshot(self) -> CloseSummary:
        self.close_summary = replace(
            self.close_summary, abandoned_producers=abandoned_provider_count(),
        )
        return self.close_summary

    def _release_resources(self) -> None:
        if self.closed:
            return
        if self.session_service is not None:
            self.session_service.event_bus.close()
        # Workers/finalizers have truly exited; never cancel queued writes or
        # pretend Python running threads can be force-killed.
        self.executor.shutdown(wait=False)
        self.closed = True

    async def aclose(self, timeout_s: float = 5.0) -> CloseSummary:
        loop = asyncio.get_running_loop()
        with self._gate:
            self.closing = True
            if self.closed:
                return self._provider_snapshot()
            service = self.session_service
            if service is not None and hasattr(service, "execution_loops"):
                supervisor = service._supervisor
                with supervisor.gate:
                    # Close admission before taking ownership evidence so no
                    # new loop can appear between this check and async close.
                    supervisor.begin_close()
                    loops = service.execution_loops()
                    self.close_summary = supervisor.close_summary()
                if self.close_summary.complete:
                    self._release_resources()
                    return self._provider_snapshot()
                if loops != frozenset({loop}):
                    # Foreign, mixed, or unproven task ownership: only observe
                    # actual completion, never await/cancel another loop's work.
                    return self._provider_snapshot()
        if service is not None:
            summary = await service.aclose(timeout_s)
            with self._gate:
                if self.closed:
                    return self._provider_snapshot()
                self.close_summary = summary
        with self._gate:
            if self.close_summary.complete:
                self._release_resources()
            return self._provider_snapshot()
