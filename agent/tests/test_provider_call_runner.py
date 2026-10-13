"""Controlled blocking providers: leases outlive callers and late results are fenced."""

from __future__ import annotations

import contextvars
import threading
from types import SimpleNamespace

import pytest

from src.providers.call_runner import (
    ProviderCallBudget,
    ProviderCallCancelled,
    ProviderCallCapacityError,
    ProviderCallRunner,
    provider_call_scope,
)
from src.providers.chat import ChatLLM, ProviderStreamError


@pytest.fixture(autouse=True)
def join_test_producers(monkeypatch):
    """Each test releases Events in finally; also join every actual SDK worker."""
    started = []
    original = threading.Thread.start

    def record_start(thread):
        if thread.name == "provider-call":
            started.append(thread)
        return original(thread)

    monkeypatch.setattr(threading.Thread, "start", record_start)
    yield started
    for thread in started:
        if thread.ident is not None:
            thread.join(3)
            assert not thread.is_alive(), "controlled provider worker leaked"


def wrapper(adapter, runner):
    client = ChatLLM.__new__(ChatLLM)
    client._llm = adapter
    client.model_name = "test"
    client._call_runner = runner
    return client


def chunk(text):
    return SimpleNamespace(content=text, tool_calls=[], additional_kwargs={}, response_metadata={}, usage_metadata=None)


@pytest.mark.parametrize("phase", ["first", "between", "fallback", "invoke"])
def test_blocking_sdk_cancel_fences_late_results_and_holds_capacity(phase):
    budget = ProviderCallBudget(1)
    runner = ProviderCallRunner(budget=budget)
    entered, release, cancel, done = (threading.Event() for _ in range(4))
    callbacks, reasoning_callbacks, results, failures = [], [], [], []
    accepted = threading.Event()

    def on_chunk(value):
        callbacks.append(value)
        accepted.set()

    class Adapter:
        def stream(self, *args, **kwargs):
            if phase == "between":
                yield chunk("accepted")
            if phase != "fallback":
                entered.set()
                assert release.wait(2)
                late_reasoning = chunk("")
                late_reasoning.additional_kwargs = {"reasoning_content": "late thought"}
                yield late_reasoning
                yield chunk("late")

        def invoke(self, *args, **kwargs):
            entered.set()
            assert release.wait(2)
            return chunk("late")

    client = wrapper(Adapter(), runner)

    def caller():
        try:
            with provider_call_scope(should_cancel=cancel.is_set):
                if phase == "invoke":
                    client.chat([{"role": "user", "content": "hi"}], timeout=0)
                else:
                    results.append(
                        client.stream_chat(
                            [],
                            should_cancel=cancel.is_set,
                            on_text_chunk=on_chunk,
                            on_reasoning_chunk=reasoning_callbacks.append,
                        )
                    )
        except ProviderCallCancelled:
            failures.append("cancelled")
        finally:
            done.set()

    caller_thread = threading.Thread(target=caller)
    caller_thread.start()
    try:
        assert entered.wait(1)
        if phase == "between":
            assert accepted.wait(1)
        cancel.set()
        assert done.wait(0.5), "cancel must return while SDK is still blocked"
        assert budget.active_count == budget.abandoned_count == 1
        with pytest.raises(ProviderCallCapacityError):
            ProviderCallRunner(budget=budget).run_call(lambda: "second")
        assert callbacks == (["accepted"] if phase == "between" else [])
        if phase != "invoke":
            assert results[0].content == ("accepted" if phase == "between" else "")
        else:
            assert failures == ["cancelled"]
    finally:
        release.set()
        caller_thread.join(2)
        assert runner.wait_for_idle(2)
    assert budget.active_count == budget.abandoned_count == 0
    assert callbacks == (["accepted"] if phase == "between" else [])
    assert reasoning_callbacks == [], "revoked SDK output must not emit late reasoning"
    assert ProviderCallRunner(budget=budget).run_call(lambda: "released") == "released"


def test_idle_deadline_before_first_chunk_and_client_close_waits_for_cleanup():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1))
    entered, release, cleaning, finish_cleanup = (threading.Event() for _ in range(4))
    closed = []

    class Client:
        def close(self):
            closed.append(True)

    class Adapter:
        _vibe_owned_http_clients = (Client(),)

        def stream(self, *args, **kwargs):
            try:
                entered.set()
                assert release.wait(2)
                yield chunk("late")
            finally:
                cleaning.set()
                assert finish_cleanup.wait(2)

    client = wrapper(Adapter(), runner)
    try:
        with pytest.raises(ProviderStreamError) as caught:
            client.stream_chat([], idle_timeout_s=0.04)
        assert isinstance(caught.value.original, TimeoutError)
        assert entered.is_set()
        client.close()
        assert closed == []
        release.set()
        assert cleaning.wait(1)
        assert runner.budget.active_count == 1
        assert closed == []
    finally:
        release.set()
        finish_cleanup.set()
        assert runner.wait_for_idle(2)
    assert closed == [True]


def test_copied_context_and_request_snapshot_at_blocking_boundary():
    tag = contextvars.ContextVar("provider-test", default="unset")
    entered, release = threading.Event(), threading.Event()
    runner = ProviderCallRunner(budget=ProviderCallBudget(1))
    messages = [{"role": "user", "content": {"nested": ["original"]}}]
    tools = [{"function": {"name": "original"}}]
    seen = []

    class Adapter:
        def bind_tools(self, supplied):
            entered.set()
            assert release.wait(2)
            seen.append(supplied)
            return self

        def invoke(self, supplied, **kwargs):
            seen.extend([tag.get(), supplied])
            return chunk("ok")

    client = wrapper(Adapter(), runner)
    result = []

    def caller():
        token = tag.set("request-context")
        try:
            result.append(client.chat(messages, tools))
        finally:
            tag.reset(token)

    thread = threading.Thread(target=caller)
    thread.start()
    try:
        assert entered.wait(1)
        messages[0]["content"]["nested"][0] = "mutated"
        tools[0]["function"]["name"] = "mutated"
    finally:
        release.set()
        thread.join(2)
        assert runner.wait_for_idle(2)
    assert seen == [
        [{"function": {"name": "original"}}],
        "request-context",
        [{"role": "user", "content": {"nested": ["original"]}}],
    ]
    assert result[0].content == "ok"


def test_cleanup_failure_releases_permit_and_is_observed():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1))

    class Iterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise StopIteration

        def close(self):
            raise RuntimeError("cleanup failed")

    with pytest.raises(RuntimeError, match="cleanup failed"):
        list(runner.stream(lambda: Iterator()))
    assert runner.wait_for_idle(1)
    assert runner.budget.active_count == 0


def test_full_payload_queue_cannot_hide_cancel_or_terminal():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1), queue_capacity=1)
    cancel = threading.Event()
    values = runner.stream(lambda: iter(range(1000)), should_cancel=cancel.is_set)
    assert next(values) == 0
    cancel.set()
    with pytest.raises(ProviderCallCancelled):
        next(values)
    assert runner.wait_for_idle(2)


def test_slow_caller_does_not_timeout_buffered_arrivals():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1), queue_capacity=2)
    values = runner.stream(lambda: iter(range(50)), idle_timeout_s=0.01)
    assert next(values) == 0
    # A controlled caller pause lets the producer fill the finite queue.
    threading.Event().wait(0.04)
    assert list(values) == list(range(1, 50))
    assert runner.wait_for_idle(1)


def test_thread_start_failure_releases_capacity(monkeypatch):
    runner = ProviderCallRunner(budget=ProviderCallBudget(1))

    def fail_start(thread):
        raise RuntimeError("start failed")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="start failed"):
        runner.run_call(lambda: "unused")
    assert runner.budget.active_count == 0
    assert runner.wait_for_idle(0.1)


def test_continuous_tiny_chunks_and_callbacks_stay_on_caller():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1), queue_capacity=2)
    caller_id = threading.get_ident()
    observed = []

    class Adapter:
        def stream(self, *args, **kwargs):
            for _ in range(100):
                yield chunk("x")

    client = wrapper(Adapter(), runner)
    # This adapter uses LangChain message addition to preserve real metadata.
    from langchain_core.messages import AIMessageChunk

    client._llm.stream = lambda *a, **kw: iter([AIMessageChunk(content="x") for _ in range(100)])
    result = client.stream_chat(
        [], idle_timeout_s=0.05, on_text_chunk=lambda value: observed.append((value, threading.get_ident()))
    )
    assert result.content == "x" * 100
    assert observed == [("x", caller_id)] * 100
    assert runner.wait_for_idle(1)


def test_default_budget_is_shared_across_wrappers_runners_and_runtime_lifespans(tmp_path, join_test_producers):
    import asyncio
    from src.api.runtime import RuntimeServices
    from src.providers.call_runner import abandoned_provider_count

    release, cancel = threading.Event(), threading.Event()
    entered = [threading.Event() for _ in range(8)]
    runners = [ProviderCallRunner() for _ in range(8)]
    done = [threading.Event() for _ in range(8)]
    threads = []

    def caller(index):
        def block():
            entered[index].set()
            assert release.wait(3)
            return "late"

        try:
            runners[index].run_call(block, should_cancel=cancel.is_set)
        except ProviderCallCancelled:
            pass
        finally:
            done[index].set()

    try:
        for i in range(8):
            thread = threading.Thread(target=caller, args=(i,))
            threads.append(thread)
            thread.start()
        assert all(event.wait(1) for event in entered)
        assert all(runner.budget is runners[0].budget for runner in runners)
        cancel.set()
        assert all(event.wait(0.5) for event in done)
        assert abandoned_provider_count() == 8
        for _ in range(2):
            runtime = RuntimeServices(tmp_path / "sessions", tmp_path / "runs")
            summary = asyncio.run(runtime.aclose())
            assert summary.complete is True
            assert summary.abandoned_producers == 8
            client = wrapper(SimpleNamespace(invoke=lambda *a, **kw: chunk("new")), ProviderCallRunner())
            with pytest.raises(ProviderCallCapacityError):
                client.chat([])
        assert abandoned_provider_count() == 8
        assert len(join_test_producers) == 8, "rejected admission must not create queued threads"
    finally:
        release.set()
        for thread in threads:
            thread.join(3)
            assert not thread.is_alive()
        assert all(runner.wait_for_idle(3) for runner in runners)
    assert abandoned_provider_count() == 0
    assert ProviderCallRunner().run_call(lambda: "free") == "free"


def test_aclose_idle_owner_awaits_async_clients():
    import asyncio

    closed = []

    class Client:
        async def aclose(self):
            await asyncio.sleep(0)
            closed.append(True)

    client = wrapper(
        SimpleNamespace(_vibe_owned_http_clients=(Client(),)), ProviderCallRunner(budget=ProviderCallBudget(1))
    )

    async def scenario():
        await client.aclose()
        assert closed == [True]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["real", "compat"])
@pytest.mark.parametrize("stop", ["cancel", "deadline"])
def test_compaction_wait_is_bounded_without_nested_capacity(tmp_path, monkeypatch, kind, stop):
    from src.agent.loop import AgentLoop
    from src.agent.tools import ToolRegistry
    from src.agent.trace import TraceWriter

    runner = ProviderCallRunner(budget=ProviderCallBudget(1))
    entered, release, done = (threading.Event() for _ in range(3))
    failures = []

    def invoke(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return chunk("late summary")

    if kind == "real":
        client = wrapper(SimpleNamespace(invoke=invoke), runner)
    else:
        client = SimpleNamespace(chat=invoke)
        # Inject one private budget for the compatibility wrapper too.
        monkeypatch.setattr("src.agent.loop.ProviderCallRunner", lambda: runner)
    agent = AgentLoop(registry=ToolRegistry(), llm=client, max_iterations=1)
    monkeypatch.setattr("src.agent.loop._llm_timeout_seconds", lambda: 0.04 if stop == "deadline" else 0)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "recent"},
        {"role": "assistant", "content": "recent answer"},
    ]
    trace = TraceWriter(tmp_path / "trace")

    def compact():
        try:
            agent._auto_compact(messages, tmp_path / "run", trace)
        except BaseException as exc:
            failures.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=compact)
    thread.start()
    try:
        assert entered.wait(1), "single-capacity compaction must admit exactly one producer"
        if stop == "cancel":
            agent.cancel()
        assert done.wait(0.5), "compaction must stop waiting while invoke is blocked"
        assert failures == []
        assert "late summary" not in str(messages)
        assert "compaction degraded" in agent._previous_summary
        assert runner.budget.active_count == runner.budget.abandoned_count == 1
    finally:
        release.set()
        thread.join(3)
        assert not thread.is_alive()
        assert runner.wait_for_idle(3)
        trace.close()
    assert "late summary" not in str(messages)


def test_owner_close_fences_a_completed_but_unconsumed_stream():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1), queue_capacity=4)
    values = runner.stream(lambda: iter(range(4)))
    assert next(values) == 0
    assert runner.wait_for_idle(1)
    runner.close_when_idle(lambda: None)
    with pytest.raises(ProviderCallCancelled):
        next(values)


def test_revoked_empty_stream_never_starts_fallback():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1))
    entered, release, cancel, done = (threading.Event() for _ in range(4))
    invoked = []

    class Adapter:
        def stream(self, *args, **kwargs):
            entered.set()
            assert release.wait(2)
            return iter(())

        def invoke(self, *args, **kwargs):
            invoked.append(True)
            return chunk("late")

    client = wrapper(Adapter(), runner)

    def caller():
        try:
            client.stream_chat([], should_cancel=cancel.is_set)
        finally:
            done.set()

    thread = threading.Thread(target=caller)
    thread.start()
    try:
        assert entered.wait(1)
        cancel.set()
        assert done.wait(0.5)
    finally:
        release.set()
        thread.join(2)
        assert runner.wait_for_idle(2)
    assert invoked == []


def test_owned_client_close_keeps_lease_until_cleanup_returns():
    runner = ProviderCallRunner(budget=ProviderCallBudget(1))
    entered, release, close_entered, close_release, done = (threading.Event() for _ in range(5))

    class Client:
        def close(self):
            close_entered.set()
            assert close_release.wait(2)

    def invoke(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return chunk("late")

    client = wrapper(SimpleNamespace(invoke=invoke, _vibe_owned_http_clients=(Client(),)), runner)

    def caller():
        try:
            client.chat([])
        except ProviderCallCancelled:
            pass
        finally:
            done.set()

    thread = threading.Thread(target=caller)
    thread.start()
    try:
        assert entered.wait(1)
        client.close()
        assert done.wait(0.5)
        release.set()
        assert close_entered.wait(1)
        assert not runner.wait_for_idle(0.03), "blocked cleanup observation must remain bounded"
        assert runner.budget.active_count == runner.budget.abandoned_count == 1
        with pytest.raises(ProviderCallCapacityError):
            ProviderCallRunner(budget=runner.budget).run_call(lambda: "new")
    finally:
        release.set()
        close_release.set()
        thread.join(2)
        assert runner.wait_for_idle(2)
    assert runner.budget.active_count == 0


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_start_failure_retains_lease_through_deferred_async_close(monkeypatch, cleanup_fails):
    import asyncio

    runner = ProviderCallRunner(budget=ProviderCallBudget(1))
    start_entered, close_admitted = threading.Event(), threading.Event()
    original_start = threading.Thread.start

    async def scenario():
        cleanup_entered, cleanup_release, cleanup_exited = (asyncio.Event() for _ in range(3))

        class Client:
            async def aclose(self):
                cleanup_entered.set()
                try:
                    await cleanup_release.wait()
                    if cleanup_fails:
                        raise RuntimeError("controlled async cleanup failure")
                finally:
                    cleanup_exited.set()

        class EarlyFailureClient:
            async def aclose(self):
                raise RuntimeError("another client failed before blocked cleanup exited")

        clients = (Client(), EarlyFailureClient()) if cleanup_fails else (Client(),)
        client = wrapper(SimpleNamespace(_vibe_owned_http_clients=clients), runner)

        def close_owner():
            assert start_entered.wait(1)
            client.close()
            close_admitted.set()

        closer = threading.Thread(target=close_owner)
        closer.start()

        def fail_start(thread):
            if thread.name != "provider-call":
                return original_start(thread)
            start_entered.set()
            assert close_admitted.wait(1), "close must be admitted before startup fails"
            raise RuntimeError("controlled start failure")

        monkeypatch.setattr(threading.Thread, "start", fail_start)
        try:
            with pytest.raises(RuntimeError, match="controlled start failure"):
                runner.run_call(lambda: "never invoked")
            await asyncio.wait_for(cleanup_entered.wait(), 0.5)
            assert not cleanup_exited.is_set()
            assert runner.budget.active_count == runner.budget.abandoned_count == 1
            assert not runner.wait_for_idle(0), "scheduled cleanup is not actual exit"
            with pytest.raises(ProviderCallCapacityError):
                ProviderCallRunner(budget=runner.budget).run_call(lambda: "must reject")
        finally:
            start_entered.set()
            cleanup_release.set()
            closer.join(1)
            assert not closer.is_alive()
            await asyncio.wait_for(cleanup_exited.wait(), 0.5)
            # Allow all cleanup completion callbacks, including error logging,
            # to execute before observing released ownership.
            for _ in range(10):
                if runner.wait_for_idle(0):
                    break
                await asyncio.sleep(0)
        assert runner.wait_for_idle(0)
        assert runner.budget.active_count == runner.budget.abandoned_count == 0
        monkeypatch.setattr(threading.Thread, "start", original_start)
        assert ProviderCallRunner(budget=runner.budget).run_call(lambda: "free") == "free"

    asyncio.run(scenario())
