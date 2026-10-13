"""Run ownership regressions with controlled local workers and storage faults."""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import src.agent.loop as loop_mod
from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry
from src.agent.trace import TraceWriter
from src.core.state import RunStateStore


def response(content="done"):
    return SimpleNamespace(content=content, tool_calls=[], has_tool_calls=False,
                           reasoning_content=None, usage_metadata=None)


def build(tmp_path, monkeypatch, llm=None):
    monkeypatch.setattr(loop_mod.ContextBuilder, "build_messages",
                        lambda *args: [{"role": "system", "content": "small"},
                                       {"role": "user", "content": "request"}])
    monkeypatch.setattr(loop_mod, "_stall_timeout_seconds", lambda: 0)
    agent = AgentLoop(ToolRegistry(), llm or SimpleNamespace(stream_chat=lambda *a, **k: response()), max_iterations=1)
    agent.memory.run_dir = str(tmp_path)
    return agent


@pytest.mark.parametrize("fault", ["request", "context", "trace_init", "trace_start", "trace_end", "state", "close"])
def test_outer_cleanup_covers_initialization_and_terminal_faults(tmp_path, monkeypatch, caplog, fault):
    agent = build(tmp_path, monkeypatch)
    def fail(*args, **kwargs):
        raise OSError(fault)
    if fault == "request":
        monkeypatch.setattr(RunStateStore, "save_request", fail)
    elif fault == "context":
        monkeypatch.setattr(loop_mod.ContextBuilder, "build_messages", fail)
    elif fault == "trace_init":
        monkeypatch.setattr(TraceWriter, "__init__", fail)
    elif fault == "trace_start":
        monkeypatch.setattr(TraceWriter, "write_text_entry", fail)
    elif fault == "trace_end":
        original = TraceWriter.write
        def write(self, record):
            if record.get("type") == "end":
                fail()
            return original(self, record)
        monkeypatch.setattr(TraceWriter, "write", write)
    elif fault == "state":
        monkeypatch.setattr(RunStateStore, "mark_success", fail)
    else:
        monkeypatch.setattr(TraceWriter, "close", fail)
    result = agent.run("request")
    assert agent._run_done.is_set(), f"{fault} stranded the owner"
    assert fault in caplog.text
    if fault in {"trace_end", "state", "close"}:
        assert result["status"] == "success"
    else:
        assert result["status"] == "failed"
        assert result["reason"] == fault
    if fault != "trace_end":
        ends = [e for e in TraceWriter.read(tmp_path) if e.get("type") == "end"]
        assert len(ends) <= 1


def test_concurrent_reuse_rejects_without_clearing_stop(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def stream(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response()
    agent = build(tmp_path, monkeypatch, SimpleNamespace(stream_chat=stream))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.run, "first")
        try:
            assert entered.wait(5)
            agent.cancel()
            with pytest.raises(RuntimeError, match="already running"):
                agent.run("second")
            assert agent._cancel_event.is_set()
            assert not agent._run_done.is_set()
        finally:
            release.set()
        assert future.result(timeout=5)["status"] == "cancelled"


def test_watchdog_stop_waits_for_main_owner_and_has_one_matching_terminal(tmp_path, monkeypatch):
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    def stream(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return response()
    agent = build(tmp_path, monkeypatch, SimpleNamespace(stream_chat=stream))
    monkeypatch.setattr(loop_mod, "_stall_timeout_seconds", lambda: 0.01)
    real_watchdog = agent._stall_watchdog
    def watchdog(*args):
        assert entered.wait(5)
        # Two watchdog polls, with no arbitrary wall-clock delay.
        done = agent._run_done
        original_wait = done.wait
        done.wait = lambda timeout=None: original_wait(0.001)
        monkeypatch.setattr(loop_mod._time, "monotonic", lambda: 1e20)
        real_watchdog(*args)
        stopped.set()
    monkeypatch.setattr(agent, "_stall_watchdog", watchdog)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.run, "first")
        try:
            assert stopped.wait(5)
            assert not future.done()
            assert not agent._run_done.is_set()
            assert not (tmp_path / "state.json").exists()
            assert not [e for e in TraceWriter.read(tmp_path) if e.get("type") == "end"]
            agent.cancel()  # A later user stop must retain the stall reason.
        finally:
            release.set()
        result = future.result(timeout=5)
    ends = [e for e in TraceWriter.read(tmp_path) if e.get("type") == "end"]
    assert len(ends) == 1
    state = json.loads((tmp_path / "state.json").read_text())
    assert result["status"] == state["status"] == ends[0]["status"] == "failed"
    assert result["reason"] == state["reason"] == ends[0]["reason"]


def test_delayed_tool_timer_cannot_emit_after_run_or_into_reuse(tmp_path, monkeypatch):
    agent = build(tmp_path, monkeypatch)
    old_emit = []
    original = agent.llm.stream_chat
    def stream(*args, **kwargs):
        old_emit.append(agent._tool_invoker().emit)
        return original(*args, **kwargs)
    agent.llm.stream_chat = stream
    events = []
    agent._event_callback = lambda name, data: events.append(name)
    agent.run("first")
    old_emit[0]("tool_heartbeat", {})
    assert "tool_heartbeat" not in events
    def reused(*args, **kwargs):
        old_emit[0]("tool_progress", {})
        return response()
    agent.llm.stream_chat = reused
    agent.run("second")
    assert "tool_progress" not in events


def test_stalled_active_write_retains_owner_until_actual_exit(tmp_path, monkeypatch):
    from src.agent.tools import BaseTool
    entered, release = threading.Event(), threading.Event()
    class Write(BaseTool):
        name = "local_write"
        description = "Controlled test write"
        parameters = {"type": "object", "properties": {}}
        is_readonly = False
        def execute(self, **kwargs):
            entered.set()
            assert release.wait(5)
            return '{"status":"success"}'
    agent = build(tmp_path, monkeypatch)
    agent.registry.register(Write())
    agent.max_iterations = 2
    tc = SimpleNamespace(id="w1", name="local_write", arguments={})
    def stream(*args, **kwargs):
        result = response("")
        result.has_tool_calls = True
        result.tool_calls = [tc]
        return result
    agent.llm.stream_chat = stream
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.run, "write")
        try:
            assert entered.wait(5)
            assert agent._lifecycle.request_stop("write stalled")
            assert not future.done()
            assert not agent._run_done.is_set()
            assert not (tmp_path / "state.json").exists()
            assert not [e for e in TraceWriter.read(tmp_path) if e.get("type") == "end"]
            with pytest.raises(RuntimeError, match="already running"):
                agent.run("competing write")
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result["status"] == "failed"
    assert result["reason"] == "write stalled"
    assert agent._run_done.is_set()
    assert len([e for e in TraceWriter.read(tmp_path) if e.get("type") == "end"]) == 1


def test_stop_after_terminal_claim_cannot_rewrite_result(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    agent = build(tmp_path, monkeypatch)
    original = RunStateStore.mark_success
    def persist(store, run_dir):
        entered.set()
        assert release.wait(5)
        original(store, run_dir)
    monkeypatch.setattr(RunStateStore, "mark_success", persist)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(agent.run, "request")
        try:
            assert entered.wait(5)
            assert not agent._lifecycle.request_stop("late stall")
            agent.cancel()
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result["status"] == "success"
    assert json.loads((tmp_path / "state.json").read_text())["status"] == "success"


def test_real_completion_only_refreshes_monotonic_progress(tmp_path, monkeypatch):
    from src.agent.tool_execution import PreparedToolCall, ToolExecutionResult
    agent = build(tmp_path, monkeypatch)
    call = PreparedToolCall(0, "read", "read", {}, True)
    monkeypatch.setattr(loop_mod._time, "monotonic", lambda: 100)
    agent._lifecycle._progress_at = 10
    for executed, outcome in [(False, "cached"), (False, "replayed"), (False, "compact"),
                              (False, "blocked"), (False, "tool_capacity"),
                              (True, "tool_timeout"), (True, "tool_cancelled")]:
        agent._observe_tool_completion(ToolExecutionResult(call, "", 0, executed, outcome))
        assert agent._lifecycle.idle_seconds() == 90
    agent._observe_tool_completion(ToolExecutionResult(call, "", 0, True, "executed"))
    assert agent._lifecycle.idle_seconds() == 0


def test_old_watchdog_paused_after_stop_cannot_poison_reused_owner(tmp_path, monkeypatch):
    first_entered, first_release = threading.Event(), threading.Event()
    second_entered, second_release = threading.Event(), threading.Event()
    stop_requested, resume_watchdog = threading.Event(), threading.Event()
    calls = 0
    def stream(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered, release = (first_entered, first_release) if calls == 1 else (second_entered, second_release)
        entered.set()
        assert release.wait(5)
        return response()
    agent = build(tmp_path, monkeypatch, SimpleNamespace(stream_chat=stream))
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(agent.run, "first")
        try:
            assert first_entered.wait(5)
            lifecycle = agent._lifecycle
            original = lifecycle.request_stop
            def request_stop(reason=None):
                accepted = original(reason)
                stop_requested.set()
                assert resume_watchdog.wait(5)
                return accepted
            monkeypatch.setattr(lifecycle, "request_stop", request_stop)
            lifecycle.done.wait = lambda timeout=None: lifecycle.done.is_set()
            monkeypatch.setattr(lifecycle, "idle_seconds", lambda: 100)
            watcher = executor.submit(agent._stall_watchdog, None, tmp_path, None, 1, lifecycle)
            assert stop_requested.wait(5)
            first_release.set()
            assert first.result(timeout=5)["status"] == "failed"
            second = executor.submit(agent.run, "second")
            assert second_entered.wait(5)
            resume_watchdog.set()
            watcher.result(timeout=5)
            assert agent._stall_reason is None
            second_release.set()
            assert second.result(timeout=5)["status"] == "success"
        finally:
            first_release.set()
            second_release.set()
            resume_watchdog.set()


def test_blocked_timer_emitter_cannot_block_stop_or_owner_finish(tmp_path, monkeypatch):
    emitted, release_emitter = threading.Event(), threading.Event()
    release_model, model_entered, cancelled = threading.Event(), threading.Event(), threading.Event()
    invokers = []
    agent = build(tmp_path, monkeypatch)
    def callback(name, data):
        if name == "tool_heartbeat":
            emitted.set()
            assert release_emitter.wait(5)
    agent._event_callback = callback
    def stream(*args, **kwargs):
        invokers.append(agent._tool_invoker())
        model_entered.set()
        assert release_model.wait(5)
        return response()
    agent.llm.stream_chat = stream
    def cancel():
        agent.cancel()
        cancelled.set()
    with ThreadPoolExecutor(max_workers=3) as executor:
        owner = executor.submit(agent.run, "request")
        try:
            assert model_entered.wait(5)
            timer = executor.submit(invokers[0].emit, "tool_heartbeat", {})
            assert emitted.wait(5)
            stop = executor.submit(cancel)
            assert cancelled.wait(0.5), "stop admission must not wait on user callback"
            stop.result(timeout=5)
            release_model.set()
            assert owner.result(timeout=0.5)["status"] == "cancelled"
            assert agent._run_done.is_set()
        finally:
            release_model.set()
            release_emitter.set()
        timer.result(timeout=5)


def test_initial_helper_predicate_is_revoked_when_first_generation_starts(tmp_path, monkeypatch):
    agent = build(tmp_path, monkeypatch)
    old_invoker = agent._tool_invoker()
    from src.agent.tool_execution import PreparedToolCall
    effects, results = [], []
    old_invoker.execute = lambda *args: effects.append("obsolete dispatch") or "{}"
    def stream(*args, **kwargs):
        results.append(old_invoker.invoke(PreparedToolCall(0, "old", "read", {}, True)))
        return response()
    agent.llm.stream_chat = stream
    assert agent.run("request")["status"] == "success"
    assert results[0].outcome == "tool_cancelled"
    assert not results[0].executed
    assert effects == []


@pytest.mark.parametrize("fault", ["state", "end", "close"])
def test_terminal_artifact_fault_still_stops_real_watchdog(tmp_path, monkeypatch, fault):
    watchdog_started, watchdog_exited = threading.Event(), threading.Event()
    agent = build(tmp_path, monkeypatch)
    monkeypatch.setattr(loop_mod, "_stall_timeout_seconds", lambda: 100)
    original_watchdog = agent._stall_watchdog
    def watchdog(*args):
        watchdog_started.set()
        try:
            original_watchdog(*args)
        finally:
            watchdog_exited.set()
    monkeypatch.setattr(agent, "_stall_watchdog", watchdog)
    def stream(*args, **kwargs):
        assert watchdog_started.wait(5)
        return response()
    agent.llm.stream_chat = stream
    def fail(*args, **kwargs):
        raise OSError("terminal artifact fault")
    if fault == "state":
        monkeypatch.setattr(RunStateStore, "mark_success", fail)
    elif fault == "close":
        original_close = TraceWriter.close
        def close(writer):
            original_close(writer)
            fail()
        monkeypatch.setattr(TraceWriter, "close", close)
    else:
        original_write = TraceWriter.write
        def write(writer, record):
            if record.get("type") == "end":
                fail()
            return original_write(writer, record)
        monkeypatch.setattr(TraceWriter, "write", write)
    assert agent.run("request")["status"] == "success"
    assert agent._run_done.is_set()
    assert watchdog_exited.wait(5)
