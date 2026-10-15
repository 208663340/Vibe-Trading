"""Session lifecycle orchestration for message flow, attempt creation, and execution scheduling.

V5: Uses AgentLoop instead of the fixed pipeline behind the generate skill.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from src.runtime.session.events import EventBus
from src.runtime.session.execution import (
    CloseSummary, ExecutionHandle, ExecutionSupervisor, PreStartCancelled,
    SessionBusyError, SessionServiceClosingError as SessionServiceClosingError,
)
from src.runtime.session.models import (
    Attempt,
    AttemptStatus,
    Message,
    Principal,
    Session,
)
from src.runtime.session.search import get_shared_index
from src.runtime.session.store import SessionStore

if TYPE_CHECKING:
    from src.runtime.agent.loop import AgentLoop

# Dedicated thread pool limited to four concurrent agents to avoid exhausting the default executor.
_AGENT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent")
logger = logging.getLogger(__name__)


#: Terminal attempt status -> SSE event name. Cancellation is its own event so
#: the UI can distinguish a user stop from a failure, both live and on reload.
_TERMINAL_EVENTS = {
    "completed": "attempt.completed",
    "cancelled": "attempt.cancelled",
    "failed": "attempt.failed",
}


class SessionService:
    """Session lifecycle service.

    Attributes:
        store: Session persistence store.
        event_bus: SSE event bus.
        runs_dir: Root runs directory.
    """

    def __init__(
        self,
        store: SessionStore,
        event_bus: EventBus,
        runs_dir: Path,
        *,
        executor: concurrent.futures.Executor | None = None,
        max_inflight: int = 16,
    ) -> None:
        """Initialize the session service.

        Args:
            store: Session persistence store.
            event_bus: SSE event bus.
            runs_dir: Root runs directory.
        """
        self.store = store
        self.event_bus = event_bus
        self.runs_dir = runs_dir
        self._supervisor = ExecutionSupervisor(store.base_dir, executor or _AGENT_EXECUTOR, max_inflight)
        try:
            # The initializing supervisor owns this store before any startup
            # resource initialization or recovery write can be observed.
            self._active_loops: Dict[str, "AgentLoop"] = {}
            self._active_tasks = self._supervisor.tasks
            self._inflight = self._supervisor.inflight
            self._inflight_lock = self._supervisor.gate
            self._search_index = get_shared_index()
            if hasattr(self._search_index, "bind_store"):
                self._search_index.bind_store(store.base_dir)
            self._recover_interrupted_attempts()
        except BaseException:
            # This runs after synchronous recovery has actually unwound; it
            # cannot retire a still-running recovery merely by requesting close.
            self._supervisor.finish_initialization(failed=True)
            raise
        else:
            self._supervisor.finish_initialization()

    def _optional(self, operation, *args, **kwargs):
        """Optional projection/notification failures cannot change durable truth."""
        try:
            return operation(*args, **kwargs)
        except Exception:
            logger.warning("Optional session operation failed", exc_info=True)
            return None

    def _recover_interrupted_attempts(self) -> None:
        """Finalize attempts that could not outlive the previous process.

        A newly constructed service has no live AgentLoop instances. Any
        attempt still marked pending or running therefore belongs to the
        previous process. Convert it to an explicit terminal state and expose
        the most recent durable response snapshot in conversation history.
        """
        recoverable = {AttemptStatus.PENDING, AttemptStatus.RUNNING}
        for attempt in self.store.list_attempts():
            if attempt.status not in recoverable:
                continue
            partial = self.store.get_partial_response(
                attempt.session_id, attempt.attempt_id
            )
            has_partial = bool(partial)
            existing_reply = self.store.get_message_for_attempt(
                attempt.session_id, attempt.attempt_id
            )
            if existing_reply is None:
                reply = Message(
                    message_id=self._terminal_message_id(attempt.attempt_id),
                    session_id=attempt.session_id,
                    role="assistant",
                    content=self._format_interrupted_message(partial),
                    linked_attempt_id=attempt.attempt_id,
                    metadata={
                        "status": AttemptStatus.INTERRUPTED.value,
                        "partial": has_partial,
                        "recovery_reason": "service_restart",
                    },
                )
                reply = self.store.append_terminal_message(reply)
                self._optional(self._search_index.index_message,
                    attempt.session_id, "assistant", reply.content
                )
                existing_reply = reply
            # Repair may adopt a previously undelimited complete terminal JSON
            # record. Always derive the terminal state from the committed reply.
            self._adopt_reply(attempt, existing_reply)
            self.store.update_attempt(attempt)
            self._optional(self.store.delete_partial_response, attempt.session_id, attempt.attempt_id)

    @staticmethod
    def _terminal_message_id(attempt_id: str) -> str:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"vibe-trading:terminal:{attempt_id}").hex[:12]

    @staticmethod
    def _format_interrupted_message(partial: Optional[str]) -> str:
        """Build the transcript message for a recovered attempt.

        Args:
            partial: Durable assistant text captured before restart.

        Returns:
            User-facing interruption notice, including partial text when any.
        """
        if partial:
            return (
                "Vibe Trading restarted before this response finished. "
                "The partial response recovered below may be incomplete:\n\n"
                f"{partial}\n\n"
                "Review it before sending a follow-up or retrying the request."
            )
        return (
            "Vibe Trading restarted before this response finished, so no "
            "complete assistant response was saved. Review any completed tool "
            "actions before retrying the request."
        )

    def create_session(
        self,
        title: str = "",
        config: Optional[Dict[str, Any]] = None,
        owner: Optional["Principal"] = None,
    ) -> Session:
        """Create a new session.

        Args:
            title: Session title.
            config: Session configuration.
            owner: Principal the session belongs to, from the authenticated
                request. Optional because sessions are also created by the CLI
                and by internal paths that have no request context; those get
                ``None``, which reads as "owner unknown" and is deliberately
                distinct from a principal that authenticated but cannot be
                attributed to a person (see ``Principal.attributable``).

        Returns:
            The newly created Session.
        """
        with self._supervisor.gate:
            self._supervisor.require_open()
            session = Session(title=title, config=config or {}, owner=owner)
            self.store.create_session(session)
            self._optional(self._search_index.index_session, session.session_id, title)
            self._optional(self.event_bus.emit, session.session_id, "session.created", {"session_id": session.session_id, "title": title})
            return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Return a session by ID."""
        return self.store.get_session(session_id)

    def list_sessions(self, limit: int = 50) -> list[Session]:
        """List all sessions."""
        return self.store.list_sessions(limit)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        with self._supervisor.gate:
            self._supervisor.require_open()
            if session_id in self._supervisor.handles:
                raise SessionBusyError(f"Session {session_id} already has a run in progress")
            deleted = self.store.delete_session(session_id)
            if deleted:
                if hasattr(self._search_index, "delete_session"):
                    self._optional(self._search_index.delete_session, session_id)
                self._optional(self.event_bus.clear, session_id)
            return deleted

    async def send_message(
        self,
        session_id: str,
        content: str,
        role: str = "user",
        *,
        include_shell_tools: bool = False,
    ) -> Dict[str, Any]:
        """Send a message to a session and trigger execution.

        Args:
            session_id: Session ID.
            content: Message content.
            role: Message role.
            include_shell_tools: Whether this attempt may use shell tools.

        Returns:
            Dictionary containing message_id and attempt_id.

        Raises:
            ValueError: If the session does not exist.
            SessionBusyError: If the session already has a run in progress.
                Callers surface this as HTTP 409; the user can wait for the
                running attempt or cancel it first.
        """
        with self._supervisor.gate:
            self._supervisor.require_open()
            session = self.store.get_session(session_id)
            if not session:
                raise ValueError(f"Session {session_id} not found")
            attempt = Attempt(session_id=session_id, parent_attempt_id=session.last_attempt_id, prompt=content) if role == "user" else None
            handle = self._supervisor.reserve(session_id, attempt.attempt_id) if attempt else None
            handed_off = False
            try:
                message = Message(session_id=session_id, role=role, content=content)
                self.store.append_message(message)
                self._optional(self._search_index.index_message, session_id, role, content)
                self._optional(self.event_bus.emit, session_id, "message.received", {"message_id": message.message_id, "role": role, "content": content})
                if attempt is None:
                    return {"message_id": message.message_id}
                self.store.create_attempt(attempt)
                session.config["include_shell_tools"] = include_shell_tools
                session.last_attempt_id = attempt.attempt_id
                session.updated_at = datetime.now().isoformat()
                self.store.update_session(session)
                self._optional(self.event_bus.emit, session_id, "attempt.created", {"attempt_id": attempt.attempt_id, "prompt": content})
                task = asyncio.create_task(self._run_attempt(session, attempt, include_shell_tools=include_shell_tools, _handle=handle))
                handle.task = task
                self._active_tasks[session_id] = task
                task.add_done_callback(lambda done: self._wrapper_done(handle, session, attempt, done))
                handed_off = True
                return {"message_id": message.message_id, "attempt_id": attempt.attempt_id}
            finally:
                if handle is not None and not handed_off:
                    handle.finalization_done = True
                    self._supervisor.release(handle)

    def get_messages(self, session_id: str, limit: int = 100) -> list[Message]:
        """Return the message history."""
        return self.store.get_messages(session_id, limit)

    def get_attempt_snapshot(self, session_id: str, attempt_id: str) -> Optional[dict]:
        """Return authoritative attempt state without private execution inputs."""
        with self._supervisor.gate:
            attempt = self.store.get_attempt(session_id, attempt_id)
            if attempt is None or self.store.get_session(session_id) is None:
                return None
            reply = self.store.get_message_for_attempt(session_id, attempt_id)
            terminal = reply is not None and reply.metadata.get("status") in {"completed", "cancelled", "failed", "interrupted"}
            metadata = reply.metadata if terminal else {}
            public_keys = ("elapsed_ms", "started_at", "run_id", "provider", "configured_model", "model", "model_source", "reasoning_effort")
            return {
                "attempt_id": attempt_id,
                "status": metadata["status"] if terminal else attempt.status.value,
                "started_at": metadata.get("started_at", attempt.started_at),
                "terminal_metadata": {key: metadata[key] for key in public_keys if key in metadata},
            }

    def cancel_current(self, session_id: str, expected_attempt_id: str | None = None) -> bool:
        """Accept cancellation atomically for the current identity."""
        with self._supervisor.gate:
            handle = self._supervisor.handles.get(session_id)
            if handle is None or handle.finalization_done:
                return False
            if handle.phase == "finalizing" and (handle.execution is None or handle.execution.done()):
                return False
            if expected_attempt_id is not None and handle.attempt_id != expected_attempt_id:
                return False
            handle.user_cancel = True
            agent = self._active_loops.get(session_id)
            if agent is not None:
                agent.cancel()
            elif handle.task is not None and not handle.task.done():
                handle.task.cancel()
            return True

    def execution_loops(self) -> frozenset[asyncio.AbstractEventLoop | None]:
        """Thread-safe task-loop evidence; construction timing is not ownership."""
        return self._supervisor.execution_loops()

    async def aclose(self, timeout_s: float = 5.0) -> CloseSummary:
        """Stop admission; a timeout retains actual writer ownership."""
        for handle in self._supervisor.begin_close():
            with self._supervisor.gate:
                handle.interrupted = True
                agent = self._active_loops.get(handle.session_id)
                if agent is not None:
                    agent.cancel()
                elif handle.task is not None and not handle.task.done():
                    handle.task.cancel()
        deadline = asyncio.get_running_loop().time() + max(0.0, timeout_s)
        while not self._supervisor.close_summary().complete and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(min(0.01, max(0.0, deadline - asyncio.get_running_loop().time())))
        return self._supervisor.close_summary()

    def _wrapper_done(self, handle, session, attempt, task):
        if not task.cancelled():
            task.exception()
        if self._supervisor.owns(handle) and not handle.finalization_done and handle.finalizer is None:
            if task.cancelled() and not handle.user_cancel:
                handle.interrupted = True
            self._start_finalizer(handle, session, attempt)

    def _start_finalizer(self, handle, session, attempt):
        handle.finalizer = asyncio.create_task(self._finish_owned(handle, session, attempt))
        def finished(task):
            if not task.cancelled():
                task.exception()
            if self._supervisor.owns(handle) and not handle.finalization_done:
                self._start_finalizer(handle, session, attempt)
        handle.finalizer.add_done_callback(finished)

    async def _finish_owned(self, handle, session, attempt):
        """Retain the claim through real execution exit and persistence."""
        handle.phase = "finalizing"
        try:
            if handle.execution is None:
                if not handle.user_cancel:
                    return
                result = {"status": "cancelled", "reason": "cancelled by user"}
            else:
                while True:
                    try:
                        result = await asyncio.shield(handle.execution)
                        break
                    except asyncio.CancelledError:
                        if not handle.execution.done():
                            # The finalizer was cancelled, not its owned execution.
                            # Retain ownership and observe the true worker outcome.
                            handle.interrupted = True
                            continue
                        if not handle.execution.cancelled():
                            continue
                        if not handle.user_cancel:
                            return
                        result = {"status": "cancelled", "reason": "cancelled by user"}
                        break
                    except PreStartCancelled:
                        if not handle.user_cancel:
                            return
                        result = {"status": "cancelled", "reason": "cancelled by user"}
                        break
                    except Exception as exc:
                        result = {"status": "failed", "reason": str(exc)}
                        break
            if handle.interrupted and not handle.user_cancel and result.get("status") == "cancelled":
                return
            future = self._supervisor.submit(handle, lambda: self._persist_outcome(session, attempt, result, handle.started_at), finalizer=True)
            wrapped = asyncio.wrap_future(future)
            while True:
                try:
                    await asyncio.shield(wrapped)
                    break
                except asyncio.CancelledError:
                    # The durable transaction owns its real I/O future until exit.
                    if future.done():
                        future.result()
                        break
        except Exception as exc:
            handle.persistence_error = exc
            logger.warning("Leaving attempt %s recoverable after terminal persistence failure", attempt.attempt_id, exc_info=True)
        finally:
            handle.finalization_done = True
            if self._supervisor.owns(handle):
                self._active_loops.pop(handle.session_id, None)
            self._supervisor.release(handle)

    async def _run_attempt(self, session: Session, attempt: Attempt, *, include_shell_tools: bool = False, _handle: ExecutionHandle | None = None) -> None:
        handle = _handle or self._supervisor.reserve(session.session_id, attempt.attempt_id)
        handle.started_at = time.perf_counter()
        try:
            attempt.mark_running()
            self.store.update_attempt(attempt)
            self._optional(self.event_bus.emit, session.session_id, "attempt.started", {"attempt_id": attempt.attempt_id, "started_at": attempt.started_at})
            handle.execution = asyncio.create_task(self._run_with_agent(attempt, messages=self.store.get_messages(session.session_id), include_shell_tools=include_shell_tools, session_config=dict(session.config)))
            await asyncio.shield(handle.execution)
        except asyncio.CancelledError:
            if not handle.user_cancel:
                handle.interrupted = True
            if handle.execution is not None and not any(not worker.done() for worker in handle.workers) and not handle.main_started:
                handle.execution.cancel()
            if handle.execution is None or (not handle.workers and not handle.main_started):
                await self._finish_owned(handle, session, attempt)
            raise
        except Exception as exc:
            if handle.execution is None:
                reason = str(exc)
                async def failed():
                    return {"status": "failed", "reason": reason}
                handle.execution = asyncio.create_task(failed())
        if not handle.finalization_done:
            await self._finish_owned(handle, session, attempt)

    def _persist_outcome(self, session, attempt, result, started_at):
        wall_started_at = attempt.started_at or time.time()
        started_at = started_at or time.perf_counter()
        status = result.get("status")
        if status == "success":
            attempt.mark_completed(summary=result.get("content", ""))
        elif status == "cancelled":
            # A cooperative cancel is not an outage; AttemptStatus.CANCELLED
            # existed but was dead because every non-success landed in the
            # failure branch.
            attempt.mark_cancelled(reason=result.get("reason", "cancelled by user"))
        else:
            attempt.mark_failed(error=result.get("reason", "unknown"))
        attempt.run_dir = result.get("run_dir")
        if result.get("metrics"):
            # Metrics were loaded from the run directory but never reached
            # the attempt, so the reply metadata below was always empty.
            attempt.metrics = result["metrics"]

        reply_metadata = {}
        if attempt.run_dir:
            reply_metadata["run_id"] = Path(attempt.run_dir).name
        reply_metadata["status"] = attempt.status.value
        if attempt.metrics:
            reply_metadata["metrics"] = attempt.metrics
        reply_metadata["elapsed_ms"] = max(0, round((time.perf_counter() - started_at) * 1000))
        reply_metadata["started_at"] = wall_started_at
        runtime_keys = (
            "provider",
            "configured_model",
            "model",
            "model_source",
            "reasoning_effort",
        )
        for key in runtime_keys:
            value = result.get(key)
            if value is not None:
                reply_metadata[key] = value

        reply = Message(
            message_id=self._terminal_message_id(attempt.attempt_id),
            session_id=session.session_id, role="assistant",
            content=self._format_result_message(attempt),
            linked_attempt_id=attempt.attempt_id,
            metadata=reply_metadata,
            # Every terminal attempt keeps its steps. A failed run saved an
            # empty trail, so after a reload the history said "0 steps"
            # for a run that made eight rounds of tool calls — hiding the
            # one thing a failure report needs.
            tool_trail=result.get("tool_trail", []),
        )
        reports = [entry["artifact"] for entry in reply.tool_trail
                   if entry.get("status") == "ok" and isinstance(entry.get("artifact"), dict)]
        if reports:
            reply.metadata["generated_reports"] = reports
            # Persist and stream the same concrete download links even if
            # the model forgot to include them in its final answer.
            links = [f"[{str(report['filename']).replace('[', '').replace(']', '')}]({report['download_url']})"
                     for report in reports]
            reply.content += "\n\n" + "\n\n".join(dict.fromkeys(links))
        self._optional(self.store.save_partial_response, session.session_id, attempt.attempt_id, reply.content)
        reply = self.store.append_terminal_message(reply)
        self._adopt_reply(attempt, reply)
        self.store.update_attempt(attempt)
        self._optional(self.store.delete_partial_response, session.session_id, attempt.attempt_id)
        self._optional(self._search_index.index_message, session.session_id, "assistant", reply.content)
        self._optional(self.event_bus.emit,
            session.session_id,
            _TERMINAL_EVENTS.get(attempt.status.value, "attempt.failed"),
            {"attempt_id": attempt.attempt_id, "status": attempt.status.value,
             "summary": reply.content, "error": attempt.error, "run_dir": attempt.run_dir,
             "started_at": wall_started_at, "ended_at": time.time(),
             **{key: reply.metadata[key] for key in ("elapsed_ms", *runtime_keys) if key in reply.metadata}},
        )

    @staticmethod
    def _adopt_reply(attempt, reply):
        status = reply.metadata.get("status")
        if status == "completed":
            attempt.mark_completed(reply.content)
        elif status == "cancelled":
            attempt.mark_cancelled("cancelled by user")
        elif status == "failed":
            attempt.mark_failed(attempt.error or "execution failed")
        else:
            attempt.mark_interrupted("service restarted before attempt completed")
        if reply.metadata.get("metrics"):
            attempt.metrics = reply.metadata["metrics"]

    async def _run_with_agent(
        self,
        attempt: Attempt,
        messages: list = None,
        *,
        include_shell_tools: bool = False,
        session_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an attempt with the V5 AgentLoop.

        Args:
            attempt: Current execution attempt.
            messages: Session message history.
            include_shell_tools: Whether the registry may include shell tools.
            session_config: Optional session-level config overrides. MCP server
                definitions under the ``mcpServers`` key are merged on top of
                the user config file via ``load_runtime_agent_config`` so each
                session can extend or override the global MCP server list.

        Returns:
            Result dictionary containing status, run_dir, run_id, metrics, and related fields.
        """
        from src.runtime.session.attempt_runner import AttemptRunner

        return await AttemptRunner(self).execute(
            attempt,
            messages,
            include_shell_tools=include_shell_tools,
            session_config=session_config,
        )

    @staticmethod
    def _record_tool_trail_event(
        tool_trail: list[Dict[str, Any]],
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Consolidate live tool events into a compact history record."""
        from src.runtime.session.attempt_runner import record_tool_trail_event

        record_tool_trail_event(tool_trail, event_type, data)

    @staticmethod
    def _convert_messages_to_history(messages: list) -> list[Dict[str, Any]]:
        """Convert Session messages into OpenAI-format history."""
        from src.runtime.session.attempt_runner import convert_messages_to_history

        return convert_messages_to_history(messages)

    @staticmethod
    def _load_metrics(run_dir: Path) -> Optional[Dict[str, Any]]:
        """Load metrics.csv from a run directory."""
        from src.runtime.session.attempt_runner import load_run_metrics

        return load_run_metrics(run_dir)

    @staticmethod
    def _format_result_message(attempt: Attempt) -> str:
        """Format the final execution result message.

        Args:
            attempt: The terminal attempt.

        Returns:
            The reply text shown in the transcript.
        """
        if attempt.status == AttemptStatus.COMPLETED:
            if attempt.summary:
                return attempt.summary
            # Do not dress an empty answer up as a finished strategy run: say
            # that nothing came back so the user knows to retry or rephrase.
            return (
                "The run finished without producing any text output. "
                "Check the run artifacts, or rephrase the request and try again."
            )
        if attempt.status == AttemptStatus.CANCELLED:
            return "Run cancelled."
        return f"Execution failed: {attempt.error or 'unknown error'}"
