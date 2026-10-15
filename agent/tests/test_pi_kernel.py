"""Pi agent-loop scheduling, without the trading tool admission pool."""

from __future__ import annotations

from src.runtime.agent.pi_kernel import (
    PiTool,
    ToolResult,
    batch_is_sequential,
    execute_tool_calls,
    run_agent_loop,
    should_terminate_batch,
)


def test_turn_order_and_follow_up_queue() -> None:
    events: list[dict] = []
    calls = {"n": 0}
    queue = [{"role": "user", "content": "after"}]

    def turn() -> str:
        calls["n"] += 1
        return "end"

    def follow_ups():
        if not queue:
            return []
        return [queue.pop(0)]

    run_agent_loop(
        turn=turn,
        max_turns=4,
        aborted=lambda: False,
        emit=events.append,
        get_follow_up_messages=follow_ups,
    )

    assert calls["n"] == 2
    assert [event["type"] for event in events] == [
        "agent_start",
        "turn_start",
        "turn_end",
        "turn_start",
        "turn_end",
        "agent_end",
    ]
    assert events[-1]["reason"] == "end"


def test_max_turns_stops_a_continue_loop() -> None:
    events: list[dict] = []
    run_agent_loop(
        turn=lambda: "continue",
        max_turns=2,
        aborted=lambda: False,
        emit=events.append,
    )
    assert events[-1] == {"type": "agent_end", "reason": "max_turns"}
    assert sum(event["type"] == "turn_start" for event in events) == 2


def test_finish_turn_can_end_a_continue() -> None:
    events: list[dict] = []
    run_agent_loop(
        turn=lambda: "continue",
        max_turns=3,
        aborted=lambda: False,
        emit=events.append,
        finish_turn=lambda action: "end",
    )
    assert events[-1]["reason"] == "end"
    assert sum(event["type"] == "turn_start" for event in events) == 1


def test_parallel_results_stay_in_source_order() -> None:
    order: list[str] = []

    def execute(name):
        def run(arguments):
            order.append(name)
            return name

        return run

    tools = [
        PiTool("first", execute("first")),
        PiTool("second", execute("second"), execution_mode="parallel"),
    ]
    results, terminate = execute_tool_calls(
        [
            {"id": "a", "name": "first", "arguments": {}},
            {"id": "b", "name": "second", "arguments": {}},
        ],
        tools,
    )
    assert [result.tool_call_id for result in results] == ["a", "b"]
    assert [result.content for result in results] == ["first", "second"]
    assert terminate is False
    assert order == ["first", "second"]


def test_one_sequential_tool_serializes_the_batch() -> None:
    assert batch_is_sequential("parallel", ["parallel", "sequential"])
    assert not batch_is_sequential("parallel", ["parallel", "parallel"])
    seen: list[str] = []

    def before(call, arguments):
        seen.append("before:" + call["name"])
        if call["name"] == "write":
            return {"block": True, "reason": "held"}
        return None

    results, terminate = execute_tool_calls(
        [
            {"id": "1", "name": "read", "arguments": {}},
            {"id": "2", "name": "write", "arguments": {}},
        ],
        [PiTool("read", lambda arguments: "page"), PiTool("write", lambda arguments: "no", execution_mode="sequential")],
        before_tool_call=before,
    )
    assert seen == ["before:read", "before:write"]
    assert results[0].content == "page"
    assert results[1].is_error is True
    assert results[1].content == "held"
    assert terminate is False


def test_terminate_requires_every_result() -> None:
    assert should_terminate_batch([]) is False
    mixed = [
        ToolResult("1", "a", "ok", terminate=True),
        ToolResult("2", "b", "ok", terminate=False),
    ]
    assert should_terminate_batch(mixed) is False
    assert should_terminate_batch([ToolResult("1", "a", "ok", terminate=True)]) is True


def test_tool_batch_runs_once_on_the_caller_then_continues() -> None:
    import threading
    from types import SimpleNamespace

    from src.runtime.agent.pi_kernel import HostToolBatch

    caller = threading.current_thread().ident
    seen: list[int | None] = []
    runs = {"n": 0}
    calls = {"n": 0}
    holder: dict = {"batch": None}

    def run() -> str:
        runs["n"] += 1
        seen.append(threading.current_thread().ident)
        return "continue"

    def turn() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            holder["batch"] = HostToolBatch(
                calls=[
                    SimpleNamespace(id="a", name="read", arguments={"path": "x"}),
                    SimpleNamespace(id="b", name="read", arguments={}),
                ],
                run=run,
            )
            return "continue"
        return "end"

    def take():
        batch = holder["batch"]
        holder["batch"] = None
        return batch

    run_agent_loop(
        turn=turn,
        max_turns=4,
        aborted=lambda: False,
        emit=lambda event: None,
        take_tool_batch=take,
    )
    assert calls["n"] == 2
    assert runs["n"] == 1
    assert seen == [caller]


def test_tool_batch_end_skips_the_next_model_turn() -> None:
    from types import SimpleNamespace

    from src.runtime.agent.pi_kernel import HostToolBatch

    calls = {"n": 0}
    holder: dict = {"batch": None}

    def turn() -> str:
        calls["n"] += 1
        holder["batch"] = HostToolBatch(
            calls=[SimpleNamespace(id="a", name="write", arguments={})],
            run=lambda: "end",
        )
        return "continue"

    def take():
        batch = holder["batch"]
        holder["batch"] = None
        return batch

    events: list[dict] = []
    run_agent_loop(
        turn=turn,
        max_turns=4,
        aborted=lambda: False,
        emit=events.append,
        take_tool_batch=take,
    )
    assert calls["n"] == 1
    assert events[-1] == {"type": "agent_end", "reason": "end"}


def test_tool_batch_failure_ends_the_run() -> None:
    from types import SimpleNamespace

    import pytest

    from src.runtime.agent.pi_kernel import HostToolBatch

    def boom() -> str:
        raise RuntimeError("batch failed")

    holder: dict = {
        "batch": HostToolBatch(
            calls=[SimpleNamespace(id="a", name="read", arguments={})],
            run=boom,
        )
    }

    def turn() -> str:
        return "continue"

    def take():
        batch = holder["batch"]
        holder["batch"] = None
        return batch

    events: list[dict] = []
    with pytest.raises(RuntimeError, match="batch failed"):
        run_agent_loop(
            turn=turn,
            max_turns=3,
            aborted=lambda: False,
            emit=events.append,
            take_tool_batch=take,
        )
    assert events[-1] == {"type": "agent_end", "reason": "error"}


def test_agent_loop_records_pi_turn_boundaries(tmp_path, monkeypatch) -> None:
    import src.runtime.agent.loop as loop_mod
    from src.runtime.agent.loop import AgentLoop
    from src.runtime.agent.tools import ToolRegistry
    from types import SimpleNamespace

    monkeypatch.setattr(
        loop_mod.ContextBuilder,
        "build_messages",
        lambda *args: [{"role": "system", "content": "small"}, {"role": "user", "content": "request"}],
    )
    monkeypatch.setattr(loop_mod, "_stall_timeout_seconds", lambda: 0)
    agent = AgentLoop(
        ToolRegistry(),
        SimpleNamespace(stream_chat=lambda *args, **kwargs: SimpleNamespace(
            content="done",
            tool_calls=[],
            has_tool_calls=False,
            reasoning_content=None,
            usage_metadata=None,
        )),
        max_iterations=1,
    )
    agent.memory.run_dir = str(tmp_path)
    result = agent.run("request")
    assert result["status"] == "success"
    kinds = [event["type"] for event in agent._pi_events]
    assert kinds[0] == "agent_start"
    assert "turn_start" in kinds
    assert kinds[-1] == "agent_end"
