"""Python host of the Pi agent loop.

Turn scheduling is ``pi_agent_core.agent_loop`` (the Python port of
``@earendil-works/pi-agent-core``): ``agent_start``, one provider turn at a
time, then ``agent_end``. The host turn returns ``continue`` or ``end``.
Steering is polled after a continued turn. A drained follow-up queue starts
one more turn when the model would otherwise stop. Provider streaming and
tool admission stay in Vibe-Trading: a write cannot be killed, and readonly
capacity is already owned by ``ToolBatchExecutor``.

Pi events are for the kernel. The trading SSE names are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from pi_agent_core import AgentContext, AgentLoopConfig, AgentToolResult, agent_loop
from pi_ai import AssistantMessage, TextContent, ToolCall, UserMessage


class PiTurn(Exception):
    """Jump to the next Pi turn, or end the run, from inside a host turn."""

    def __init__(self, action: str) -> None:
        if action not in {"continue", "end"}:
            raise ValueError(f"unsupported turn action: {action}")
        self.action = action
        super().__init__(action)


@dataclass
class ToolResult:
    """One tool result in assistant source order."""

    tool_call_id: str
    name: str
    content: str
    is_error: bool = False
    terminate: bool = False


@dataclass
class PiTool:
    name: str
    execute: Callable[[dict[str, Any]], str]
    execution_mode: str = "parallel"
    prepare_arguments: Callable[[dict[str, Any]], dict[str, Any]] | None = None


@dataclass
class PiKernelConfig:
    """Hooks with the same jobs as ``AgentLoopConfig`` in pi-agent-core."""

    convert_to_llm: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
    transform_context: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None
    before_tool_call: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None] | None = None
    after_tool_call: Callable[[dict[str, Any], ToolResult], ToolResult | None] | None = None
    finish_turn: Callable[[str], str | None] | None = None
    tool_execution: str = "parallel"
    get_steering_messages: Callable[[], list[dict[str, Any]]] | None = None
    get_follow_up_messages: Callable[[], list[dict[str, Any]]] | None = None
    tools: list[PiTool] = field(default_factory=list)


def convert_roles(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the roles an LLM can read. Custom transcript rows stay out of the request."""
    return [message for message in messages if message.get("role") in {"system", "user", "assistant", "tool"}]


def batch_is_sequential(tool_execution: str, modes: list[str]) -> bool:
    """Pi runs the whole batch sequentially when any call asks for it."""
    return tool_execution == "sequential" or any(mode == "sequential" for mode in modes)


def should_terminate_batch(results: list[ToolResult]) -> bool:
    """Stop the follow-up model call only when every result in the batch says so."""
    return bool(results) and all(result.terminate for result in results)


class _QuietAssistantStream:
    """One assistant message with no tool calls, so Pi does not run tools again."""

    def __init__(self, message: AssistantMessage) -> None:
        self._message = message
        self._pending = True

    def __aiter__(self) -> _QuietAssistantStream:
        return self

    async def __anext__(self) -> Any:
        if not self._pending:
            raise StopAsyncIteration
        self._pending = False
        return _DoneEvent()

    async def result(self) -> AssistantMessage:
        return self._message


class _DoneEvent:
    type = "done"


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content", "")
        return content if isinstance(content, str) else ""
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else ""


def _accept_turn(future: asyncio.Future[str], action: str) -> None:
    if not future.done():
        future.set_result(action)


def _reject_turn(future: asyncio.Future[str], exc: BaseException) -> None:
    if not future.done():
        future.set_exception(exc)


@dataclass
class HostToolBatch:
    """One assistant tool batch. ``run`` commits it on the caller thread.

    Pi schedules the calls. ``run`` is the trading batch: grounding admission
    and readonly replay stay in its prepare and commit steps, and writes still
    wait for the earlier result.
    """

    calls: list[Any]
    run: Callable[[], str]


class _SdkToolBatch:
    """Hop Pi's per-call execute back to one caller-thread batch."""

    def __init__(self, host: HostToolBatch, requests: queue.Queue[Any], state: dict[str, Any]) -> None:
        self.host = host
        self.requests = requests
        self.state = state
        self.lock: asyncio.Lock | None = None
        self.started = False
        self.action = "continue"
        self.fatal: BaseException | None = None

    def tools(self) -> list[_PiRegistryTool]:
        names: list[str] = []
        seen: set[str] = set()
        for call in self.host.calls:
            name = str(getattr(call, "name", "") or "")
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
        return [_PiRegistryTool(name, self) for name in names]

    async def execute(self) -> None:
        if self.lock is None:
            self.lock = asyncio.Lock()
        async with self.lock:
            if self.started:
                return
            self.started = True
            loop = asyncio.get_running_loop()
            future: asyncio.Future[str] = loop.create_future()
            self.requests.put(("tools", loop, future, self))
            await future
            if self.fatal is not None:
                self.state["fatal"] = self.fatal
            elif self.action == "end":
                self.state["stop_after_tools"] = True


class _PiRegistryTool:
    """Registry name Pi can call. The body is the shared host batch."""

    def __init__(self, name: str, batch: _SdkToolBatch) -> None:
        self.name = name
        self.description = name
        self.parameters: dict[str, Any] = {"type": "object", "properties": {}}
        self.label = name
        self.execution_mode = "parallel"
        self._batch = batch

    async def execute(
        self,
        tool_call_id: str,
        params: dict[str, Any],
        cancel_event: asyncio.Event | None = None,
        on_update: Any = None,
    ) -> AgentToolResult:
        del tool_call_id, params, cancel_event, on_update
        await self._batch.execute()
        # terminate so Pi does not start another model call by itself.
        # A continued turn is a steering message, same as a text-only continue.
        return AgentToolResult(content=[TextContent(text="")], terminate=True)


def _tool_arguments(call: Any) -> dict[str, Any]:
    arguments = getattr(call, "arguments", None)
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def execute_tool_calls(
    calls: list[dict[str, Any]],
    tools: list[PiTool],
    *,
    tool_execution: str = "parallel",
    aborted: Callable[[], bool] | None = None,
    emit: Callable[[dict[str, Any]], None] | None = None,
    before_tool_call: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None] | None = None,
    after_tool_call: Callable[[dict[str, Any], ToolResult], ToolResult | None] | None = None,
) -> tuple[list[ToolResult], bool]:
    """Preflight in source order, run Pi's parallel or sequential mode, commit in source order.

    Completion may finish out of order. Returned results follow the assistant's
    call order, which is the guarantee Pi states for ``turn_end.toolResults``.
    """
    stopped = aborted or (lambda: False)
    publish = emit or (lambda event: None)
    by_name = {tool.name: tool for tool in tools}
    sequential = batch_is_sequential(
        tool_execution,
        [by_name[call["name"]].execution_mode for call in calls if call["name"] in by_name],
    )
    if sequential:
        return _execute_sequential(calls, by_name, stopped, publish, before_tool_call, after_tool_call)
    return _execute_parallel(calls, by_name, stopped, publish, before_tool_call, after_tool_call)


def run_agent_loop(
    *,
    turn: Callable[[], str],
    max_turns: int,
    aborted: Callable[[], bool],
    emit: Callable[[dict[str, Any]], None],
    finish_turn: Callable[[str], str | None] | None = None,
    get_steering_messages: Callable[[], list[dict[str, Any]]] | None = None,
    get_follow_up_messages: Callable[[], list[dict[str, Any]]] | None = None,
    take_tool_batch: Callable[[], HostToolBatch | None] | None = None,
) -> None:
    """Run Pi's outer loop until the host turn ends, the cap is hit, or the run is aborted.

    The host turn records its own cancellation, so a stop flag does not skip
    that turn. ``aborted`` labels ``agent_end`` when the turn itself ends.
    ``get_steering_messages`` is polled after a turn that continued. A drained
    follow-up queue starts one more turn when the model would otherwise stop.
    """
    if max_turns < 1:
        raise ValueError("max_turns must be positive")
    state: dict[str, Any] = {"action": "end", "turns": 0, "reason": "end", "invalid": None, "index": 0}
    ended = False

    def publish(event: dict[str, Any]) -> None:
        nonlocal ended
        if event["type"] == "agent_end":
            if ended:
                return
            ended = True
        emit(event)

    requests: queue.Queue[Any] = queue.Queue()
    pi_tools: list[Any] = []

    async def stream_turn(model: Any, context: Any, options: Any) -> _QuietAssistantStream:
        del model, context, options
        # The trading turn stays on the caller thread. It is synchronous and
        # may itself call asyncio.run. Pi's loop only waits for that result.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        requests.put(("turn", loop, future))
        action = await future
        state["turns"] += 1
        state["action"] = action
        if action not in {"continue", "end"}:
            state["invalid"] = action
        batch = take_tool_batch() if action == "continue" and take_tool_batch is not None else None
        if action != "continue" and take_tool_batch is not None:
            take_tool_batch()
        pi_tools.clear()
        if batch is None:
            return _QuietAssistantStream(
                AssistantMessage(content=[TextContent(text="")], stop_reason="stop")
            )
        sdk_batch = _SdkToolBatch(batch, requests, state)
        pi_tools.extend(sdk_batch.tools())
        content: list[Any] = [
            ToolCall(
                id=str(getattr(call, "id", "") or ""),
                name=str(getattr(call, "name", "") or ""),
                arguments=_tool_arguments(call),
            )
            for call in batch.calls
        ]
        return _QuietAssistantStream(AssistantMessage(content=content, stop_reason="toolUse"))

    def steering() -> list[UserMessage]:
        # Pi polls steering before the first assistant response. Nothing has run yet.
        if state["turns"] == 0 or state["invalid"] is not None or state["action"] != "continue":
            return []
        if get_steering_messages is not None:
            get_steering_messages()
        if state["turns"] >= max_turns:
            state["reason"] = "max_turns"
            return []
        return [UserMessage(content="", timestamp=0)]

    def follow_ups() -> list[UserMessage]:
        if state["invalid"] is not None or state["action"] != "end" or state["reason"] == "max_turns":
            return []
        pending = list(get_follow_up_messages() or []) if get_follow_up_messages else []
        if not pending:
            return []
        if state["turns"] >= max_turns:
            state["reason"] = "max_turns"
            return []
        return [UserMessage(content=_message_text(item), timestamp=0) for item in pending]

    def reject_bad_action(_info: dict[str, Any]) -> bool:
        if state["invalid"] is not None:
            raise ValueError(f"unsupported turn action: {state['invalid']}")
        fatal = state.get("fatal")
        if fatal is not None:
            raise fatal
        if state.get("stop_after_tools"):
            state["stop_after_tools"] = False
            state["action"] = "end"
            return True
        return False

    def translate(event: Any) -> None:
        kind = event.type
        if kind == "agent_start":
            publish({"type": "agent_start"})
        elif kind == "turn_start":
            state["index"] += 1
            publish({"type": "turn_start", "turn": state["index"]})
        elif kind == "turn_end":
            publish({"type": "turn_end", "turn": state["index"], "action": state["action"]})
        elif kind == "agent_end":
            reason = state["reason"]
            if reason == "end" and aborted():
                reason = "aborted"
            publish({"type": "agent_end", "reason": reason})

    config = AgentLoopConfig(
        get_steering_messages=steering,
        get_follow_up_messages=follow_ups,
        should_stop_after_turn=reject_bad_action,
    )

    async def drive() -> None:
        stream = agent_loop(
            [],
            AgentContext(system_prompt="", messages=[], tools=pi_tools),
            config,
            stream_fn=stream_turn,
        )
        async for event in stream:
            translate(event)
        await stream.result()

    failure: list[BaseException] = []

    def run_pi_loop() -> None:
        try:
            asyncio.run(drive())
        except Exception as exc:
            publish({"type": "agent_end", "reason": "error"})
            failure.append(exc)
        finally:
            requests.put(None)

    pi_loop = threading.Thread(target=run_pi_loop, name="pi-agent-loop")
    pi_loop.start()
    try:
        while True:
            item = requests.get()
            if item is None:
                break
            kind = item[0]
            if kind == "tools":
                _loop, tool_future, tool_batch = item[1], item[2], item[3]
                try:
                    tool_action = tool_batch.host.run()
                except Exception as exc:
                    tool_batch.fatal = exc
                    tool_action = "end"
                else:
                    tool_batch.fatal = None
                tool_batch.action = tool_action
                _loop.call_soon_threadsafe(_accept_turn, tool_future, tool_action)
                continue
            loop, future = item[1], item[2]
            try:
                action = turn()
                if finish_turn is not None:
                    decision = finish_turn(action)
                    if decision is not None:
                        action = decision
            except Exception as exc:
                loop.call_soon_threadsafe(_reject_turn, future, exc)
                continue
            loop.call_soon_threadsafe(_accept_turn, future, action)
    finally:
        pi_loop.join()
    if failure:
        raise failure[0]


def _execute_sequential(calls, by_name, stopped, publish, before_tool_call, after_tool_call):
    results: list[ToolResult] = []
    for call in calls:
        if stopped():
            break
        results.append(_one_call(call, by_name, stopped, publish, before_tool_call, after_tool_call))
    return results, should_terminate_batch(results)


def _execute_parallel(calls, by_name, stopped, publish, before_tool_call, after_tool_call):
    prepared: list[tuple[dict[str, Any], ToolResult | None]] = []
    for call in calls:
        publish({
            "type": "tool_execution_start",
            "toolCallId": call["id"],
            "toolName": call["name"],
            "args": call.get("arguments") or {},
        })
        blocked = _preflight(call, by_name, before_tool_call)
        prepared.append((call, blocked))
        if blocked is not None:
            finalized = _after(call, blocked, after_tool_call)
            publish({
                "type": "tool_execution_end",
                "toolCallId": call["id"],
                "isError": finalized.is_error,
            })
            prepared[-1] = (call, finalized)
        if stopped():
            break
    runnable = [index for index, item in enumerate(prepared) if item[1] is None]
    # Pi finalizes parallel calls as they complete, then emits tool results in source order.
    # These tools are synchronous, so completion order is the start order unless a call raises.
    for index in runnable:
        call = prepared[index][0]
        if stopped():
            result = ToolResult(call["id"], call["name"], "Operation aborted", is_error=True)
        else:
            result = _run_tool(call, by_name)
        result = _after(call, result, after_tool_call)
        publish({
            "type": "tool_execution_end",
            "toolCallId": call["id"],
            "isError": result.is_error,
        })
        prepared[index] = (call, result)
    messages = [item[1] for item in prepared if item[1] is not None]
    for result in messages:
        publish({
            "type": "message_end",
            "role": "toolResult",
            "toolCallId": result.tool_call_id,
        })
    return messages, should_terminate_batch(messages)


def _one_call(call, by_name, stopped, publish, before_tool_call, after_tool_call) -> ToolResult:
    publish({
        "type": "tool_execution_start",
        "toolCallId": call["id"],
        "toolName": call["name"],
        "args": call.get("arguments") or {},
    })
    if stopped():
        result = ToolResult(call["id"], call["name"], "Operation aborted", is_error=True)
    else:
        blocked = _preflight(call, by_name, before_tool_call)
        result = blocked if blocked is not None else _run_tool(call, by_name)
    result = _after(call, result, after_tool_call)
    publish({"type": "tool_execution_end", "toolCallId": call["id"], "isError": result.is_error})
    publish({"type": "message_end", "role": "toolResult", "toolCallId": result.tool_call_id})
    return result


def _preflight(call, by_name, before_tool_call) -> ToolResult | None:
    tool = by_name.get(call["name"])
    arguments = dict(call.get("arguments") or {})
    if tool is None:
        return ToolResult(call["id"], call["name"], f"Tool {call['name']} not found", is_error=True)
    if tool.prepare_arguments is not None:
        arguments = tool.prepare_arguments(arguments)
        call["arguments"] = arguments
    if before_tool_call is None:
        return None
    decision = before_tool_call(call, arguments)
    if not decision:
        return None
    if decision.get("block"):
        return ToolResult(
            call["id"],
            call["name"],
            str(decision.get("reason") or "Tool execution was blocked"),
            is_error=True,
            terminate=bool(decision.get("terminate")),
        )
    return None


def _run_tool(call, by_name) -> ToolResult:
    tool = by_name[call["name"]]
    try:
        content = tool.execute(dict(call.get("arguments") or {}))
    except Exception as exc:  # noqa: BLE001 - one tool error does not drop the rest of the batch
        return ToolResult(call["id"], call["name"], str(exc), is_error=True)
    return ToolResult(call["id"], call["name"], content or "")


def _after(call, result: ToolResult, after_tool_call) -> ToolResult:
    if after_tool_call is None:
        return result
    replacement = after_tool_call(call, result)
    return result if replacement is None else replacement
