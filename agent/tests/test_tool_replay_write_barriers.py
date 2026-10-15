"""Replay observations linearize at dispatch across real write effects."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.tests.test_readonly_replay_after_microcompact import _Context, _Trace, _loop


class Registry:
    def __init__(self, failure=None):
        self.value = "old"
        self.effects = []
        self.failure = failure

    def get(self, name):
        return SimpleNamespace(
            is_readonly=name != "write", repeatable=True, deterministic=name == "calc", replay_after_compaction=True
        )

    def execute(self, name, args):
        self.effects.append(name)
        if name == "write":
            self.value = "new"
            if self.failure == "exception":
                raise RuntimeError("partial effect")
            if self.failure == "error":
                return '{"status":"error","error":"partial effect"}'
        return json.dumps({"status": "ok", "value": self.value})


def _call(name, i):
    return SimpleNamespace(name=name, id=str(i), arguments={})


def _seed(loop):
    key = loop._identical_call_key("read", {})
    loop._readonly_replay_cache[key] = '{"status":"ok","value":"old"}'
    loop._readonly_replay_ready.add(key)
    return key


@pytest.mark.parametrize("failure", [None, "error", "exception"])
def test_write_then_replay_ready_read_observes_actual_new_value(failure):
    registry = Registry(failure)
    loop = _loop(registry)
    _seed(loop)
    messages, trace = [], _Trace()
    loop._process_tool_calls([_call("write", 0), _call("read", 1)], _Context(), messages, trace, [], 1)
    assert registry.effects == ["write", "read"]
    assert [m["tool_call_id"] for m in messages] == ["0", "1"]
    assert json.loads(messages[1]["content"])["value"] == "new"
    assert not any(e["type"] == "tool_result_replayed" for e in trace.events)


def test_replay_ready_read_before_write_keeps_request_order():
    registry = Registry()
    loop = _loop(registry)
    _seed(loop)
    messages = []
    loop._process_tool_calls([_call("read", 0), _call("write", 1)], _Context(), messages, _Trace(), [], 1)
    assert [m["tool_call_id"] for m in messages] == ["0", "1"]
    assert json.loads(messages[0]["content"])["value"] == "old"
    assert registry.effects == ["write"]


def test_denied_write_does_not_invalidate_replay():
    registry = Registry()
    loop = _loop(registry)
    _seed(loop)
    loop._grounding = SimpleNamespace(
        authorized_symbols=set(),
        identity_status="not_required",
        authorize_tool_call=lambda name, *a, **kw: SimpleNamespace(
            allowed=name != "write", error_payload=lambda *a: '{"status":"error","error_code":"denied"}'
        ),
        identity_summary=lambda: {},
        ingest_tool_result=lambda **kw: None,
    )
    messages, trace = [], _Trace()
    loop._process_tool_calls([_call("write", 0), _call("read", 1)], _Context(), messages, trace, [], 1)
    assert registry.effects == []
    assert json.loads(messages[1]["content"])["value"] == "old"
    assert any(e["type"] == "tool_result_replayed" for e in trace.events)


def test_cancelled_before_write_preserves_ready_observation():
    loop = _loop(Registry())
    key = _seed(loop)
    loop._cancel_event.set()
    loop._process_tool_calls([_call("write", 0)], _Context(), [], _Trace(), [], 1)
    assert loop.registry.effects == []
    assert key in loop._readonly_replay_ready


def test_executed_write_keeps_pure_deterministic_cache():
    registry = Registry("error")
    loop = _loop(registry)
    key = loop._identical_call_key("calc", {})
    loop._called_identical[key] = '{"status":"ok","value":"pure"}'
    messages = []
    loop._process_tool_calls([_call("write", 0), _call("calc", 1)], _Context(), messages, _Trace(), [], 1)
    assert registry.effects == ["write"]
    assert json.loads(messages[1]["content"])["value"] == "pure"


def test_cancel_at_write_admission_does_not_expire_observation():
    registry = Registry()
    loop = _loop(registry)
    key = _seed(loop)
    loop._event_callback = lambda kind, payload: loop._cancel_event.set() if kind == "tool_call" else None
    messages = []
    loop._process_tool_calls([_call("write", 0)], _Context(), messages, _Trace(), [], 1)
    assert registry.effects == []
    assert key in loop._readonly_replay_ready
    assert json.loads(messages[0]["content"])["error_code"] == "tool_cancelled"


def test_mutation_invalidates_before_execute_even_if_result_persistence_fails():
    registry = Registry()
    loop = _loop(registry)
    key = _seed(loop)
    original_execute = registry.execute
    dispatch_observation = []

    def execute(name, args):
        dispatch_observation.append(key in loop._readonly_replay_cache)
        return original_execute(name, args)

    registry.execute = execute
    trace = _Trace()

    def fail_persist(**kwargs):
        raise OSError("result persistence failed")

    trace.write_tool_result = fail_persist
    with pytest.raises(OSError, match="result persistence failed"):
        loop._process_tool_calls([_call("write", 0)], _Context(), [], trace, [], 1)
    assert registry.effects == ["write"]
    assert dispatch_observation == [False]
    assert key not in loop._readonly_replay_ready
    assert key not in loop._readonly_replay_cache


def test_cancel_during_authorization_prevents_replay_admission():
    registry = Registry()
    loop = _loop(registry)
    key = _seed(loop)

    def authorize(*args, **kwargs):
        loop._cancel_event.set()
        return SimpleNamespace(allowed=True)

    loop._grounding = SimpleNamespace(
        authorized_symbols=set(),
        identity_status="not_required",
        authorize_tool_call=authorize,
        ingest_tool_result=lambda **kw: None,
    )
    messages, trace = [], _Trace()
    loop._process_tool_calls([_call("read", 0)], _Context(), messages, trace, [], 1)
    assert json.loads(messages[0]["content"])["error_code"] == "tool_cancelled"
    assert key in loop._readonly_replay_ready
    assert loop._readonly_replay_recoveries == 0
    assert registry.effects == []


def test_unknown_tool_compatibility_invocation_preserves_observations():
    from src.runtime.agent.tools import ToolRegistry

    loop = _loop(ToolRegistry())
    # Empty registries are falsey, so install the actual empty registry after
    # constructing the test facade. Unknown execute returns its real diagnostic.
    loop.registry = ToolRegistry()
    key = _seed(loop)
    raw, _ = loop._invoke_tool("unknown", {}, call_id="unknown-call")
    assert "not found" in json.loads(raw)["error"]
    assert key in loop._readonly_replay_ready
    assert key in loop._readonly_replay_cache
