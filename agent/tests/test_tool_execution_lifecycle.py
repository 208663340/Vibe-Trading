"""Controlled execution races; all blocked workers are released in finally."""

from __future__ import annotations

import contextvars
import json
import threading
import time
from types import SimpleNamespace

from src.agent import tuning
from src.agent.progress import emit_progress
from agent.tests.test_readonly_replay_after_microcompact import _Context, _Trace, _loop


def _call(i, name="read"):
    return SimpleNamespace(id=str(i), name=name, arguments={"i": i})


class Registry:
    def __init__(self, operation):
        self.operation = operation

    def get(self, name):
        return SimpleNamespace(is_readonly=name != "write", repeatable=True, deterministic=False)

    def execute(self, name, args):
        return self.operation(name, args)


def _start(loop, calls, messages, trace):
    errors = []

    def run():
        try:
            loop._process_tool_calls(calls, _Context(), messages, trace, [], 1)
        except BaseException as exc:
            errors.append(exc)

    caller = threading.Thread(target=run)
    caller.start()
    return caller, errors


def _until(predicate, timeout=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        threading.Event().wait(0.002)
    return predicate()


def test_fast_completion_observed_before_slow_first_with_ordered_commit(monkeypatch):
    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0, raising=False)
    slow = threading.Event()
    fast = threading.Event()

    def operation(name, args):
        if args["i"] == 0:
            assert slow.wait(2)
        else:
            fast.set()
        return json.dumps({"status": "ok", "value": args["i"]})

    loop = _loop(Registry(operation))
    loop._last_activity_wall = 0
    messages, trace = [], _Trace()
    caller, errors = _start(loop, [_call(0), _call(1)], messages, trace)
    try:
        assert fast.wait(0.5)
        assert _until(lambda: loop._last_activity_wall > 0), "fast result must refresh progress before slow result"
        assert messages == [], "ordered commit must wait for first call"
    finally:
        slow.set()
        caller.join(2)
    assert not caller.is_alive()
    assert errors == []
    assert [m["tool_call_id"] for m in messages] == ["0", "1"]
    assert [e["call_id"] for e in trace.events if e["type"] == "tool_result"] == ["0", "1"]


def test_cancel_never_invokes_queued_ninth_tool(monkeypatch):
    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0, raising=False)
    release = threading.Event()
    lock = threading.Lock()
    started = []

    def operation(name, args):
        with lock:
            started.append(args["i"])
        assert release.wait(2)
        return '{"status":"ok"}'

    loop = _loop(Registry(operation))
    messages, trace = [], _Trace()
    caller, errors = _start(loop, [_call(i) for i in range(12)], messages, trace)
    try:
        assert _until(lambda: len(started) >= 8)
        assert len(started) == 8
        loop._cancel_event.set()
        assert _until(lambda: not caller.is_alive()), "cancelled read waits must return with timeout disabled"
    finally:
        release.set()
        caller.join(2)
    assert errors == []
    assert sorted(started) == list(range(8))
    assert [m["tool_call_id"] for m in messages] == [str(i) for i in range(12)]


def test_zero_timeout_cancel_fences_late_progress(monkeypatch):
    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0, raising=False)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def operation(name, args):
        entered.set()
        try:
            assert release.wait(2)
            emit_progress("late", message="late result")
            return '{"status":"ok","value":"late"}'
        finally:
            exited.set()

    loop = _loop(Registry(operation))
    events = []
    loop._event_callback = lambda kind, data: events.append((kind, data))
    messages, trace = [], _Trace()
    caller, errors = _start(loop, [_call(0)], messages, trace)
    try:
        assert entered.wait(0.5)
        loop._cancel_event.set()
        assert _until(lambda: not caller.is_alive()), "timeout=0 still polls cancellation"
        count = len(events)
    finally:
        release.set()
        caller.join(2)
        assert exited.wait(0.5)
    assert errors == []
    assert len(events) == count
    assert json.loads(messages[0]["content"])["error_code"] == "tool_cancelled"


def test_running_write_finishes_but_next_tools_never_launch(monkeypatch):
    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0, raising=False)
    entered, release = threading.Event(), threading.Event()
    effects = []

    def operation(name, args):
        effects.append(name)
        if name == "write":
            entered.set()
            assert release.wait(2)
            effects.append("completed")
        return '{"status":"ok"}'

    loop = _loop(Registry(operation))
    messages, trace = [], _Trace()
    caller, errors = _start(loop, [_call(0, "write"), _call(1)], messages, trace)
    try:
        assert entered.wait(0.5)
        loop._cancel_event.set()
        caller.join(0.05)
        assert caller.is_alive(), "active write remains owned"
    finally:
        release.set()
        caller.join(2)
    assert errors == []
    assert effects == ["write", "completed"]
    assert [m["tool_call_id"] for m in messages] == ["0", "1"]


def test_read_worker_receives_copied_context(monkeypatch):
    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0, raising=False)
    marker = contextvars.ContextVar("tool-test-marker", default="missing")
    seen = []
    loop = _loop(Registry(lambda name, args: seen.append(marker.get()) or '{"status":"ok"}'))
    token = marker.set("request-context")
    try:
        loop._process_tool_calls([_call(0), _call(1)], _Context(), [], _Trace(), [], 1)
    finally:
        marker.reset(token)
    assert seen == ["request-context", "request-context"]


def test_default_budget_survives_loop_and_runtime_instances(monkeypatch, tmp_path):
    import asyncio
    from src.agent.tool_execution import abandoned_readonly_tool_count
    from src.api.runtime import RuntimeServices
    from src.session.execution import CloseSummary

    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0, raising=False)
    release = threading.Event()
    workers, loops, callers = [], [], []
    initial = abandoned_readonly_tool_count()

    def operation(name, args):
        workers.append(threading.current_thread())
        assert release.wait(3)
        return '{"status":"ok"}'

    async def snapshot():
        first = RuntimeServices(tmp_path / "one", tmp_path / "runs-one")
        second = RuntimeServices(tmp_path / "two", tmp_path / "runs-two")
        assert (await first.aclose(0)).abandoned_readers == initial + 32
        assert (await first.aclose(0)).abandoned_readers == initial + 32
        assert (await second.aclose(0)).abandoned_readers == initial + 32
        # A foreign owner may only be observed; its unfinished writer count
        # remains independent of the abandoned process readers.
        third = RuntimeServices(tmp_path / "three", tmp_path / "runs-three")
        supervisor = SimpleNamespace(
            gate=threading.RLock(), begin_close=lambda: None, close_summary=lambda: CloseSummary(False, ("foreign",))
        )
        third.session_service = SimpleNamespace(_supervisor=supervisor, execution_loops=lambda: frozenset({object()}))
        summary = await third.aclose(0)
        assert not summary.complete
        assert summary.pending_run_writers == ("foreign",)
        assert summary.abandoned_readers == initial + 32
        third.executor.shutdown(wait=False)
        return first

    try:
        for i in range(32):
            entered = threading.Event()

            def execute(name, args, entered=entered):
                entered.set()
                return operation(name, args)

            loop = _loop(Registry(execute))
            loops.append(loop)
            caller, errors = _start(loop, [_call(i)], [], _Trace())
            callers.append(caller)
            assert entered.wait(0.5)
            loop._cancel_event.set()
            caller.join(0.5)
            assert not caller.is_alive()
            assert errors == []
        first = asyncio.run(snapshot())
        extra = _loop(Registry(operation))
        raw, _ = extra._invoke_tool("read", {}, call_id="exhausted")
        assert json.loads(raw)["error_code"] == "tool_capacity"
        assert len(workers) == 32
    finally:
        for loop in loops:
            loop._cancel_event.set()
        release.set()
        for caller in callers:
            caller.join(1)
        for worker in workers:
            worker.join(1)
    assert abandoned_readonly_tool_count() == initial
    assert asyncio.run(first.aclose(0)).abandoned_readers == initial
    raw, _ = _loop(Registry(lambda *_: '{"status":"ok"}'))._invoke_tool("read", {}, call_id="freed")
    assert json.loads(raw)["status"] == "ok"


def test_start_failure_releases_reader_capacity(monkeypatch):
    from src.agent.tool_execution import ReadonlyToolBudget

    budget = ReadonlyToolBudget(1)
    loop = _loop(Registry(lambda *_: '{"status":"ok"}'))
    loop._readonly_tool_budget = budget
    start = threading.Thread.start

    def fail_start(self):
        raise RuntimeError("start failed")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    messages = []
    loop._process_tool_calls([_call(0)], _Context(), messages, _Trace(), [], 1)
    assert "start failed" in messages[0]["content"]
    assert budget.active_count == budget.abandoned_count == 0
    monkeypatch.setattr(threading.Thread, "start", start)
    raw, _ = loop._invoke_tool("read", {}, call_id="retry")
    assert json.loads(raw)["status"] == "ok"


def test_raw_grounding_and_ordered_commit_are_caller_owned(monkeypatch):
    import src.agent.loop as loop_module

    caller = threading.get_ident()
    ingestion = []
    raw = '{"status":"ok","value":"raw evidence"}'
    loop = _loop(Registry(lambda *_: raw))
    loop._grounding = SimpleNamespace(
        authorized_symbols=set(),
        identity_status="not_required",
        authorize_tool_call=lambda *a, **kw: SimpleNamespace(allowed=True),
        ingest_tool_result=lambda **kw: ingestion.append((threading.get_ident(), kw["call_id"], kw["result"])),
    )
    monkeypatch.setattr(loop_module, "truncate_tool_result", lambda result: "truncated")
    monkeypatch.setattr(loop_module, "redact_tool_result", lambda result: "redacted")
    messages, trace = [], _Trace()
    loop._process_tool_calls([_call(0), _call(1)], _Context(), messages, trace, [], 1)
    assert ingestion == [(caller, "0", raw), (caller, "1", raw)]
    assert [m["content"] for m in messages] == ["truncated", "truncated"]
    assert [e["result"] for e in trace.events if e["type"] == "tool_result"] == ["redacted", "redacted"]


def test_synthetic_and_executed_results_keep_call_identity_and_order(monkeypatch):
    from src.agent import tuning

    monkeypatch.setattr(tuning, "TOKEN_THRESHOLD", 100, raising=False)
    loop = _loop(Registry(lambda *_: '{"status":"ok"}'))
    calc = loop._identical_call_key("calc", {})
    read = loop._identical_call_key("replay", {})
    loop._called_identical[calc] = '{"status":"ok","value":"calculated"}'
    loop._readonly_replay_cache[read] = '{"status":"ok","value":"restored"}'
    loop._readonly_replay_ready.add(read)
    original_get = loop.registry.get

    def get(name):
        definition = original_get(name)
        definition.deterministic = name == "calc"
        definition.repeatable = False
        return definition

    loop.registry.get = get
    loop._grounding = SimpleNamespace(
        authorized_symbols=set(),
        identity_status="not_required",
        authorize_tool_call=lambda name, *a, **kw: SimpleNamespace(
            allowed=name != "denied", error_payload=lambda *a: '{"status":"error","error_code":"denied"}'
        ),
        identity_summary=lambda: {},
        ingest_tool_result=lambda **kw: None,
    )
    calls = [
        SimpleNamespace(name=name, id=str(i), arguments={})
        for i, name in enumerate(["read", "calc", "replay", "denied", "compact", "read"])
    ]
    messages, trace = [], _Trace()
    loop._process_tool_calls(calls, _Context(), messages, trace, [], 1)
    assert [m["tool_call_id"] for m in messages] == ["0", "1", "2", "3", "4", "5"]
    terminal_types = {
        "tool_result",
        "tool_result_cached",
        "tool_result_replayed",
        "tool_skipped",
        "compact_requested",
        "compact_declined",
    }
    assert [e.get("call_id") for e in trace.events if e["type"] in terminal_types] == ["0", "1", "2", "3", "4", "5"]


def test_timed_out_live_readers_keep_eight_batch_slots(monkeypatch):
    from src.agent.tool_execution import ReadonlyToolBudget

    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0.03, raising=False)
    release = threading.Event()
    workers = []

    def operation(name, args):
        workers.append(threading.current_thread())
        assert release.wait(2)
        return '{"status":"ok"}'

    loop = _loop(Registry(operation))
    budget = ReadonlyToolBudget(32)
    loop._readonly_tool_budget = budget
    messages, trace = [], _Trace()
    try:
        loop._process_tool_calls([_call(i) for i in range(16)], _Context(), messages, trace, [], 1)
        assert len(workers) == 8, "timed-out producers still own batch capacity"
        assert budget.active_count == budget.abandoned_count == 8
        assert [json.loads(m["content"])["error_code"] for m in messages] == ["tool_timeout"] * 8 + [
            "tool_capacity"
        ] * 8
        assert [m["tool_call_id"] for m in messages] == [str(i) for i in range(16)]
    finally:
        release.set()
        for worker in workers:
            worker.join(1)
    assert budget.active_count == 0


def test_worker_progress_is_bounded_while_caller_callback_stalls(monkeypatch):
    monkeypatch.setattr(tuning, "TOOL_TIMEOUT_SECONDS", 0, raising=False)
    callback_entered, release_callback, flood_done = threading.Event(), threading.Event(), threading.Event()
    workers, events = [], []

    def operation(name, args):
        workers.append(threading.current_thread())
        emit_progress("initial", current=0)
        assert callback_entered.wait(1)
        for i in range(1, 10001):
            emit_progress("working", current=i)
        flood_done.set()
        return '{"status":"ok"}'

    def emit(kind, data):
        if kind == "tool_progress":
            events.append((threading.get_ident(), data["current"]))
            if data["stage"] == "initial":
                callback_entered.set()
                assert release_callback.wait(2)

    loop = _loop(Registry(operation))
    loop._event_callback = emit
    caller, errors = _start(loop, [_call(0)], [], _Trace())
    try:
        assert callback_entered.wait(0.5)
        assert flood_done.wait(0.5)
        assert len(events) == 1
    finally:
        release_callback.set()
        caller.join(2)
        for worker in workers:
            worker.join(1)
    assert errors == []
    assert 1 < len(events) <= 33, "stalled callback cannot accumulate unbounded progress"
    assert events[-1][1] == 10000
    assert all(thread_id == caller.ident for thread_id, _ in events)
