"""Application ownership survives partial startup and incomplete shutdown."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import src.interfaces.api_server as api_server
from src.interfaces.api import state
from src.runtime.session.events import EventBus
from src.runtime.session.execution import CloseSummary


def test_lifespan_closes_initialized_session_after_inbound_failure(monkeypatch):
    order = []

    async def startup():
        order.append("startup")
        raise RuntimeError("partial startup")

    async def stop():
        order.append("inbound-stop")
        raise RuntimeError("inbound stop failed")

    async def session_close(timeout_s=5):
        order.append("session-close")
        return CloseSummary(True)

    bus = EventBus()
    service = SimpleNamespace(aclose=session_close, event_bus=bus)
    monkeypatch.setattr(api_server, "_session_service", service)
    monkeypatch.setattr(state, "_session_service", service)
    monkeypatch.setattr(api_server, "_run_startup_preflight", startup)
    monkeypatch.setattr(api_server, "_stop_scheduled_research_on_shutdown", stop)

    async def scenario():
        with pytest.raises(RuntimeError):
            async with api_server._lifespan(api_server.app):
                pass

    asyncio.run(scenario())
    assert order == ["startup", "inbound-stop", "session-close"]
    assert api_server._session_service is None
    assert state._session_service is None


def test_runtime_incomplete_close_retains_resources_until_true_completion(tmp_path):
    from src.interfaces.api.runtime import RuntimeServices

    async def scenario():
        runtime = RuntimeServices(tmp_path / "sessions", tmp_path / "runs")
        bus = EventBus()
        finished = False

        async def close(timeout_s=5):
            return CloseSummary(finished, () if finished else ("a",))

        runtime.session_service = SimpleNamespace(aclose=close, event_bus=bus)
        assert not (await runtime.aclose(0)).complete
        assert runtime.session_service is not None
        assert not runtime.executor._shutdown
        assert not bus._closed
        finished = True
        assert (await runtime.aclose(0)).complete
        assert runtime.executor._shutdown
        assert bus._closed
        assert (await runtime.aclose(0)).complete

    asyncio.run(scenario())


def test_runtime_close_keeps_actual_worker_and_finalizer_then_rebuilds(tmp_path, monkeypatch):
    import threading
    from src.interfaces.api.runtime import RuntimeServices
    from src.runtime.session.execution import SessionServiceClosingError
    from src.runtime.session.search import SessionSearchIndex

    index = SessionSearchIndex(tmp_path / "index.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    started, release, finalizing, finish = (threading.Event() for _ in range(4))

    async def scenario():
        runtime = RuntimeServices(tmp_path / "sessions", tmp_path / "runs")
        service = runtime.get_session_service()
        assert service._supervisor.executor is runtime.executor

        async def run(attempt, **kwargs):
            def worker():
                started.set()
                assert release.wait(2)
                return {"status": "success", "content": "done"}

            handle = service._supervisor.handles[attempt.session_id]
            return await asyncio.shield(asyncio.wrap_future(service._supervisor.submit(handle, worker, main=True)))

        monkeypatch.setattr(service, "_run_with_agent", run)
        original = service.store.append_terminal_message

        def append(message):
            finalizing.set()
            assert finish.wait(2)
            return original(message)

        monkeypatch.setattr(service.store, "append_terminal_message", append)
        session = service.create_session()
        sent = await service.send_message(session.session_id, "go")
        try:
            assert await asyncio.to_thread(started.wait, 1)
            summary = await runtime.aclose(0)
            assert summary.pending_run_writers == (sent["attempt_id"],)
            assert not service.event_bus._closed
            with pytest.raises(SessionServiceClosingError):
                runtime.get_session_service()
            competitor = RuntimeServices(runtime.sessions_dir, runtime.runs_dir)
            try:
                with pytest.raises(SessionServiceClosingError):
                    competitor.get_session_service()
            finally:
                await competitor.aclose(0)
            release.set()
            assert await asyncio.to_thread(finalizing.wait, 1)
            summary = await runtime.aclose(0)
            assert not summary.complete
            assert summary.pending_finalizers == (sent["attempt_id"],)
            assert not runtime.executor._shutdown
        finally:
            release.set()
            finish.set()
            assert (await runtime.aclose(1)).complete
        successor = RuntimeServices(runtime.sessions_dir, runtime.runs_dir)
        try:
            assert successor.get_session_service() is not service
        finally:
            await successor.aclose(1)

    try:
        asyncio.run(scenario())
    finally:
        index.close()


def test_later_lifespan_on_another_loop_refuses_unfinished_then_rebuilds(tmp_path, monkeypatch):
    import threading
    from src.interfaces.api.runtime import RuntimeServices
    from src.runtime.session.execution import SessionServiceClosingError
    from src.runtime.session.search import SessionSearchIndex

    index = SessionSearchIndex(tmp_path / "index.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    monkeypatch.setattr(state, "_runtime_services", None)
    monkeypatch.setattr(state, "_session_service", None)
    monkeypatch.setattr(api_server, "_session_service", None)
    monkeypatch.setattr(api_server, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")
    loop = asyncio.new_event_loop()
    started, release = threading.Event(), threading.Event()
    holder = {}

    async def begin():
        runtime = RuntimeServices(tmp_path / "sessions", tmp_path / "runs")
        state._runtime_services = runtime
        service = runtime.get_session_service()
        state._session_service = api_server._session_service = service
        holder["runtime"] = runtime

        async def run(attempt, **kwargs):
            def worker():
                started.set()
                assert release.wait(3)
                return {"status": "success", "content": "done"}

            handle = service._supervisor.handles[attempt.session_id]
            return await asyncio.shield(asyncio.wrap_future(service._supervisor.submit(handle, worker, main=True)))

        monkeypatch.setattr(service, "_run_with_agent", run)
        original_close = service.aclose

        async def close_on_owner(timeout_s=5):
            assert asyncio.get_running_loop() is loop, "old-loop session tasks accessed from another loop"
            return await original_close(timeout_s)

        monkeypatch.setattr(service, "aclose", close_on_owner)
        session = service.create_session()
        await service.send_message(session.session_id, "go")
        assert await asyncio.to_thread(started.wait, 1)
        assert not (await runtime.aclose(0)).complete

    def owner_thread():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(begin())
        loop.run_forever()

    thread = threading.Thread(target=owner_thread)
    thread.start()
    try:
        assert started.wait(1)

        # Wait for the close request itself on the owner loop, using a barrier.
        async def ready():
            while not holder["runtime"].closing:
                await asyncio.sleep(0)

        asyncio.run_coroutine_threadsafe(ready(), loop).result(1)
        with pytest.raises(SessionServiceClosingError):
            asyncio.run(state._prepare_runtime_services())
        assert state._runtime_services is holder["runtime"]
        assert not holder["runtime"].executor._shutdown
        release.set()
        assert asyncio.run_coroutine_threadsafe(holder["runtime"].aclose(1), loop).result(2).complete
        replacement = asyncio.run(state._prepare_runtime_services())
        assert state._runtime_services is replacement
        assert replacement is not holder["runtime"]
        asyncio.run(state._close_runtime_services(1))
    finally:
        release.set()
        if "runtime" in holder:
            asyncio.run_coroutine_threadsafe(holder["runtime"].aclose(1), loop).result(2)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()
        index.close()


def test_shutdown_resources_close_once_and_second_lifespan_has_fresh_service(tmp_path, monkeypatch):
    from src.interfaces.api.runtime import RuntimeServices
    from src.runtime.session.search import SessionSearchIndex

    index = SessionSearchIndex(tmp_path / "index.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    monkeypatch.setattr(state, "_runtime_services", None)
    monkeypatch.setattr(state, "_session_service", None)
    monkeypatch.setattr(api_server, "_session_service", None)
    monkeypatch.setattr(api_server, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")

    async def startup():
        pass

    async def stop():
        pass

    monkeypatch.setattr(api_server, "_run_startup_preflight", startup)
    monkeypatch.setattr(api_server, "_stop_scheduled_research_on_shutdown", stop)
    calls = []
    original = RuntimeServices._release_resources

    def release(runtime):
        calls.append(runtime)
        original(runtime)

    monkeypatch.setattr(RuntimeServices, "_release_resources", release)

    async def scenario():
        async with api_server._lifespan(api_server.app):
            first_runtime = api_server.app.state.runtime_services
            first_service = first_runtime.get_session_service()
            state._session_service = api_server._session_service = first_service
        assert first_runtime.closed
        assert state._session_service is api_server._session_service is None
        assert (await first_runtime.aclose(0)).complete
        async with api_server._lifespan(api_server.app):
            second_runtime = api_server.app.state.runtime_services
            assert second_runtime is not first_runtime
            assert second_runtime.get_session_service() is not first_service
        assert calls == [first_runtime, second_runtime]

    try:
        asyncio.run(scenario())
    finally:
        index.close()


def test_legacy_host_none_reset_rebuilds_idle_service_with_owned_executor(tmp_path, monkeypatch):
    from src.runtime.session.search import SessionSearchIndex

    index = SessionSearchIndex(tmp_path / "index.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    monkeypatch.setattr(state, "_runtime_services", None)
    monkeypatch.setattr(state, "_session_service", None)
    monkeypatch.setattr(api_server, "_session_service", None)
    monkeypatch.setattr(api_server, "SESSIONS_DIR", tmp_path / "sessions")
    monkeypatch.setattr(api_server, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(
        state, "get_env_config", lambda: SimpleNamespace(api=SimpleNamespace(enable_session_runtime=True))
    )
    try:
        first = state._get_session_service()
        first_runtime = state._runtime_services
        api_server._session_service = None
        second = state._get_session_service()
        assert second is not first
        assert first_runtime.closed
        assert second._supervisor.executor is state._runtime_services.executor
    finally:
        asyncio.run(state._close_runtime_services(1))
        index.close()


@pytest.mark.parametrize("construction", ["synchronous", "idle_other_loop"])
def test_close_uses_actual_execution_loop_after_lazy_construction(tmp_path, monkeypatch, construction):
    import threading
    from src.interfaces.api.runtime import RuntimeServices
    from src.runtime.session.execution import SessionServiceClosingError
    from src.runtime.session.search import SessionSearchIndex

    index = SessionSearchIndex(tmp_path / "index.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    runtime = RuntimeServices(tmp_path / "sessions", tmp_path / "runs")
    if construction == "synchronous":
        service = runtime.get_session_service()
    else:

        async def idle_creation():
            return runtime.get_session_service()

        service = asyncio.run(idle_creation())
    loop = asyncio.new_event_loop()
    loop.set_debug(True)
    ready, release = threading.Event(), threading.Event()
    holder = {}

    async def run(attempt, **kwargs):
        def worker():
            ready.set()
            assert release.wait(3)
            return {"status": "success", "content": "done"}

        handle = service._supervisor.handles[attempt.session_id]
        return await asyncio.shield(asyncio.wrap_future(service._supervisor.submit(handle, worker, main=True)))

    monkeypatch.setattr(service, "_run_with_agent", run)
    real_close = service.aclose

    async def verified_close(timeout_s=5):
        # Assert before the real cancellation to avoid stranding test cleanup
        # on the baseline's non-thread-safe Future.cancel() reproduction.
        assert asyncio.get_running_loop() is loop, "foreign loop touched real execution close"
        return await real_close(timeout_s)

    monkeypatch.setattr(service, "aclose", verified_close)

    async def begin():
        session = service.create_session()
        holder["session"] = session
        holder["sent"] = await service.send_message(session.session_id, "go")

    def owner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(begin())
        loop.run_forever()

    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert ready.wait(1)
        summary = asyncio.run(runtime.aclose(0))
        assert not summary.complete
        assert holder["sent"]["attempt_id"] in summary.pending_run_writers
        assert not runtime.executor._shutdown

        async def close_on_owner():
            result = await runtime.aclose(0)
            assert service._supervisor.handles[holder["session"].session_id].interrupted
            return result

        assert not asyncio.run_coroutine_threadsafe(close_on_owner(), loop).result(1).complete
        competitor = RuntimeServices(runtime.sessions_dir, runtime.runs_dir)
        try:
            with pytest.raises(SessionServiceClosingError):
                competitor.get_session_service()
        finally:
            asyncio.run(competitor.aclose(0))
        release.set()
        assert asyncio.run_coroutine_threadsafe(runtime.aclose(1), loop).result(2).complete
        successor = RuntimeServices(runtime.sessions_dir, runtime.runs_dir)
        try:
            assert successor.get_session_service() is not service
        finally:
            asyncio.run(successor.aclose(1))
    finally:
        release.set()
        assert asyncio.run_coroutine_threadsafe(real_close(1), loop).result(2).complete
        asyncio.run(runtime.aclose(0))
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()
        index.close()


def test_unproven_execution_loop_keeps_reserved_owner_and_resources(tmp_path, monkeypatch):
    from src.interfaces.api.runtime import RuntimeServices
    from src.runtime.session.search import SessionSearchIndex

    index = SessionSearchIndex(tmp_path / "index.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    runtime = RuntimeServices(tmp_path / "sessions", tmp_path / "runs")
    service = runtime.get_session_service()

    async def scenario():
        session = service.create_session()
        unknown = service._supervisor.reserve(session.session_id, "reserved")

        async def forbidden_close(timeout_s=5):
            raise AssertionError("unproven task ownership was treated as caller-owned")

        monkeypatch.setattr(service, "aclose", forbidden_close)
        try:
            summary = await runtime.aclose(0)
            assert not summary.complete
            assert summary.pending_run_writers == ("reserved",)
            assert not runtime.executor._shutdown
            assert not service.event_bus._closed
        finally:
            unknown.finalization_done = True  # Confirm never submitted in this fixture.
            assert service._supervisor.release(unknown)
            assert (await runtime.aclose(0)).complete

    try:
        asyncio.run(scenario())
    finally:
        index.close()
