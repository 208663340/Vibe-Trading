"""Assemble and run one session attempt.

SessionService admits, cancels, and commits the durable transcript. This
runner builds the registry, model client, and AgentLoop, forwards events
while the attempt still owns the session, and reads optional run metrics.
"""

from __future__ import annotations

import asyncio
import csv
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.runtime.session.checkpoint import ResponseCheckpoint
from src.runtime.session.execution import PreStartCancelled
from src.runtime.session.models import Attempt

# Roughly 3000 tokens of prior conversation, kept from the newest turns.
_MAX_HISTORY_CHARS = 12000


class AttemptRunner:
    """One attempt's agent assembly. The host remains the session coordinator."""

    def __init__(self, host: Any) -> None:
        self.host = host

    async def execute(
        self,
        attempt: Attempt,
        messages: list | None = None,
        *,
        include_shell_tools: bool = False,
        session_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute an attempt and return the existing result dictionary.

        Imports stay inside the call so tests can replace ``build_registry``,
        ``ChatLLM``, and ``AgentLoop`` on their defining modules.
        """
        from src.runtime.agent.loop import AgentLoop
        from src.platform.config.loader import load_runtime_agent_config, sanitize_session_overrides
        from src.runtime.memory.persistent import PersistentMemory
        from src.runtime.providers.chat import ChatLLM
        from src.runtime.tools import build_registry

        host = self.host
        llm = ChatLLM()
        pm = PersistentMemory()

        session_id = attempt.session_id
        attempt_id = attempt.attempt_id
        handle = host._supervisor.handles.get(session_id)
        if handle is not None and handle.attempt_id != attempt_id:
            handle = None
        callbacks_live = True
        tool_trail: list[Dict[str, Any]] = []
        checkpoint = ResponseCheckpoint(host.store, attempt)

        safe_overrides = sanitize_session_overrides(session_config) if session_config else session_config
        agent_config = load_runtime_agent_config(overrides=safe_overrides)

        def event_callback(event_type: str, data: Dict[str, Any]) -> None:
            """Forward AgentLoop events to the SSE event bus."""
            with host._supervisor.gate:
                if not callbacks_live or (handle is not None and not host._supervisor.owns(handle)):
                    return
                host._optional(checkpoint.handle_event, event_type, data)
                if event_type in {"tool_call", "tool_result"}:
                    host._record_tool_trail_event(tool_trail, event_type, data)
                host._optional(
                    host.event_bus.emit,
                    session_id,
                    event_type,
                    {**data, "attempt_id": attempt_id},
                )

        def _mcp_collision_warn(msg: str) -> None:
            """Forward MCP server-name collision warnings to the operator event channel."""
            event_callback("mcp.warning", {"message": msg})

        async def worker(fn, *, main=False):
            future = host._supervisor.submit(handle, fn, main=main)
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
            with host._supervisor.gate:
                if handle is not None and (handle.user_cancel or handle.interrupted):
                    raise PreStartCancelled()
                agent = AgentLoop(
                    registry=registry,
                    llm=llm,
                    event_callback=event_callback,
                    max_iterations=50,
                    persistent_memory=pm,
                )
                host._active_loops[session_id] = agent
            history = host._convert_messages_to_history(messages) if messages else None
            result = await worker(
                lambda: agent.run(
                    user_message=attempt.prompt,
                    history=history,
                    session_id=session_id,
                ),
                main=True,
            )
        finally:
            with host._supervisor.gate:
                callbacks_live = False
                if handle is None or host._supervisor.owns(handle):
                    host._active_loops.pop(session_id, None)
                host._optional(checkpoint.flush)
            close_llm = getattr(llm, "close", None)
            if callable(close_llm):
                host._optional(close_llm)

        result["tool_trail"] = tool_trail

        if result.get("run_dir"):
            metrics = host._load_metrics(Path(result["run_dir"]))
            if metrics:
                result["metrics"] = metrics

        return result


def record_tool_trail_event(
    tool_trail: list[Dict[str, Any]],
    event_type: str,
    data: Dict[str, Any],
) -> None:
    """Consolidate live tool events into a compact history record."""
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


def convert_messages_to_history(messages: list) -> list[Dict[str, Any]]:
    """Convert session messages into OpenAI-format history within a character budget."""

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

    total_chars = 0
    trimmed: list = []
    for msg in reversed(history):
        content = msg.get("content", "")
        remaining = _MAX_HISTORY_CHARS - total_chars
        if len(content) <= remaining:
            trimmed.append(msg)
            total_chars += len(content)
            continue
        if not trimmed:
            trimmed.append({**msg, "content": content[:remaining] + "\n[... truncated]"})
        break
    return list(reversed(trimmed))


def load_run_metrics(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Load metrics.csv. One non-numeric field is skipped, not the whole row."""
    metrics_path = run_dir / "artifacts" / "metrics.csv"
    if not metrics_path.exists():
        return None
    try:
        with open(metrics_path, "r", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
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
