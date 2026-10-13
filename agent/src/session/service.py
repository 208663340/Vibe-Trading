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

from src.session.checkpoint import ResponseCheckpoint
from src.session.events import EventBus
from src.session.execution import (
    CloseSummary, ExecutionHandle, ExecutionSupervisor, PreStartCancelled,
    SessionBusyError, SessionServiceClosingError as SessionServiceClosingError,
)
from src.session.models import (
    Attempt,
    AttemptStatus,
    Message,
    Principal,
    Session,
)
from src.session.search import get_shared_index
from src.session.store import SessionStore

if TYPE_CHECKING:
    from src.agent.loop import AgentLoop

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
        # Legacy handles are views; the supervisor owns execution identities.
        self._active_loops: Dict[str, "AgentLoop"] = {}
        self._active_tasks = self._supervisor.tasks
        self._inflight = self._supervisor.inflight
        self._inflight_lock = self._supervisor.gate
        self._search_index = get_shared_index()
        if hasattr(self._search_index, "bind_store"):
            self._search_index.bind_store(store.base_dir)
        try:
            self._recover_interrupted_attempts()
        except BaseException:
            self._supervisor.begin_close()
            raise

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
        from src.tools import build_registry
        from src.providers.chat import ChatLLM
        from src.agent.loop import AgentLoop
        from src.memory.persistent import PersistentMemory
        from src.config.loader import load_runtime_agent_config, sanitize_session_overrides

        llm = ChatLLM()
        pm = PersistentMemory()

        session_id = attempt.session_id
        attempt_id = attempt.attempt_id
        handle = self._supervisor.handles.get(session_id)
        if handle is not None and handle.attempt_id != attempt_id:
            handle = None
        callbacks_live = True
        tool_trail: list[Dict[str, Any]] = []
        checkpoint = ResponseCheckpoint(self.store, attempt)

        safe_overrides = sanitize_session_overrides(session_config) if session_config else session_config
        agent_config = load_runtime_agent_config(overrides=safe_overrides)

        def event_callback(event_type: str, data: Dict[str, Any]) -> None:
            """Forward AgentLoop events to the SSE event bus."""
            with self._supervisor.gate:
                if not callbacks_live or (handle is not None and not self._supervisor.owns(handle)):
                    return
                self._optional(checkpoint.handle_event, event_type, data)
                if event_type in {"tool_call", "tool_result"}:
                    self._record_tool_trail_event(tool_trail, event_type, data)
                self._optional(self.event_bus.emit, session_id, event_type, {**data, "attempt_id": attempt_id})

        def _mcp_collision_warn(msg: str) -> None:
            """Forward MCP server-name collision warnings to the operator event channel."""
            event_callback("mcp.warning", {"message": msg})

        async def worker(fn, *, main=False):
            future = self._supervisor.submit(handle, fn, main=main)
            wrapped = asyncio.wrap_future(future)
            while True:
                try:
                    return await asyncio.shield(wrapped)
                except asyncio.CancelledError:
                    # Cancelling the waiter cannot cancel a running thread.
                    if handle is not None:
                        handle.interrupted = True
                    if future.done():
                        return future.result()

        try:
            registry = await worker(lambda: build_registry(
                persistent_memory=pm,
                include_shell_tools=include_shell_tools,
                agent_config=agent_config,
                session_id=session_id,
                event_callback=event_callback,
                warn_callback=_mcp_collision_warn,
            ))
            with self._supervisor.gate:
                if handle is not None and (handle.user_cancel or handle.interrupted):
                    raise PreStartCancelled()
                agent = AgentLoop(registry=registry, llm=llm, event_callback=event_callback,
                                  max_iterations=50, persistent_memory=pm)
                self._active_loops[session_id] = agent
            history = self._convert_messages_to_history(messages) if messages else None
            result = await worker(lambda: agent.run(
                    user_message=attempt.prompt,
                    history=history,
                    session_id=session_id,
                ), main=True)
        finally:
            with self._supervisor.gate:
                callbacks_live = False
                if handle is None or self._supervisor.owns(handle):
                    self._active_loops.pop(session_id, None)
                self._optional(checkpoint.flush)
            close_llm = getattr(llm, "close", None)
            if callable(close_llm):
                self._optional(close_llm)

        result["tool_trail"] = tool_trail

        # Load metrics from the run output when available.
        if result.get("run_dir"):
            metrics = self._load_metrics(Path(result["run_dir"]))
            if metrics:
                result["metrics"] = metrics

        return result

    @staticmethod
    def _record_tool_trail_event(
        tool_trail: list[Dict[str, Any]],
        event_type: str,
        data: Dict[str, Any],
    ) -> None:
        """Consolidate live tool events into a compact history record.

        Args:
            tool_trail: Mutable per-attempt trail.
            event_type: Agent event type (`tool_call` or `tool_result`).
            data: Already-redacted live event payload.
        """
        tool = str(data.get("tool") or "")
        if not tool:
            return

        call_id_value = data.get("call_id")
        call_id = call_id_value if isinstance(call_id_value, str) and call_id_value else None

        if event_type == "tool_call":
            entry: Dict[str, Any] = {
                "tool": tool,
                "status": "running",
                "arguments": (
                    dict(data["arguments"])
                    if isinstance(data.get("arguments"), dict)
                    else {}
                ),
                "timestamp": int(time.time() * 1000),
            }
            if call_id:
                entry["call_id"] = call_id
            tool_trail.append(entry)
            return

        match = None
        if call_id:
            match = next(
                (
                    entry
                    for entry in tool_trail
                    if entry.get("call_id") == call_id
                    and entry.get("status") == "running"
                ),
                None,
            )
        if match is None:
            match = next(
                (
                    entry
                    for entry in tool_trail
                    if entry.get("tool") == tool
                    and entry.get("status") == "running"
                ),
                None,
            )
        if match is None:
            match = {
                "tool": tool,
                "arguments": {},
                "timestamp": int(time.time() * 1000),
            }
            tool_trail.append(match)

        match["status"] = "ok" if data.get("status") == "ok" else "error"
        elapsed_ms = data.get("elapsed_ms")
        if isinstance(elapsed_ms, (int, float)) and not isinstance(elapsed_ms, bool):
            match["elapsed_ms"] = max(0, int(elapsed_ms))
        match["preview"] = str(data.get("preview") or "")
        if match["status"] == "ok" and isinstance(data.get("artifact"), dict):
            match["artifact"] = dict(data["artifact"])
        if call_id:
            match["call_id"] = call_id

    @staticmethod
    def _convert_messages_to_history(messages: list) -> list[Dict[str, Any]]:
        """Convert Session messages into OpenAI-format history.

        Keeps the readable ``[prev_run: {run_id}]`` marker instead of removing it
        completely, and trims by character budget instead of a hard six-message cap
        so the LLM can still see previous artifact paths and strategy content during
        iterative updates.

        Args:
            messages: Session message list without the current turn.

        Returns:
            OpenAI-format messages trimmed from the newest items within the token budget.
        """
        import re
        from pathlib import Path

        def _shorten_run_dir(match: re.Match) -> str:
            path_str = match.group(0).replace("Run directory:", "").strip()
            run_id = Path(path_str).name if path_str else ""
            return f"[prev_run: {run_id}]" if run_id else ""

        history = []
        for msg in messages[:-1]:
            role = msg.role if hasattr(msg, "role") else msg.get("role", "user")
            content = msg.content if hasattr(msg, "content") else msg.get("content", "")
            if not content.strip() or role not in ("user", "assistant"):
                continue
            content = re.sub(r"Run directory:\s*\S+", _shorten_run_dir, content).strip()
            if content:
                history.append({"role": role, "content": content})

        # Trim from the newest messages within a character budget of roughly 3000 tokens.
        MAX_HISTORY_CHARS = 12000
        total_chars = 0
        trimmed: list = []
        for msg in reversed(history):
            content = msg.get("content", "")
            remaining = MAX_HISTORY_CHARS - total_chars
            if len(content) <= remaining:
                trimmed.append(msg)
                total_chars += len(content)
                continue
            # A single oversized message must not wipe the whole window: when
            # nothing has been kept yet, the newest turn survives truncated
            # rather than the agent starting with no history at all.
            if not trimmed:
                trimmed.append({**msg, "content": content[:remaining] + "\n[... truncated]"})
            break
        return list(reversed(trimmed))

    @staticmethod
    def _load_metrics(run_dir: Path) -> Optional[Dict[str, Any]]:
        """Load metrics.csv from a run directory.

        A run's metrics row can carry a non-numeric column (e.g.
        ``benchmark_ticker``) alongside the numeric metrics, so each field is
        converted independently: one unconvertible field is skipped rather
        than discarding every metric in the row.
        """
        import csv
        metrics_path = run_dir / "artifacts" / "metrics.csv"
        if not metrics_path.exists():
            return None
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            return None
        if not rows:
            return None
        metrics: Dict[str, Any] = {}
        for key, value in rows[0].items():
            if not value:
                continue
            try:
                metrics[key] = float(value)
            except (TypeError, ValueError):
                continue
        return metrics or None

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
