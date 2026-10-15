"""Execution ownership and durable outcome regressions, using isolated stores."""
from __future__ import annotations

import asyncio
import json
import threading

import pytest

from src.runtime.session.events import EventBus
from src.runtime.session.models import Attempt, AttemptStatus, Message
from src.runtime.session.search import SessionSearchIndex
from src.runtime.session.service import SessionBusyError, SessionService, SessionServiceClosingError
from src.runtime.session.store import SessionStore


@pytest.fixture
def service(tmp_path, monkeypatch):
    index = SessionSearchIndex(tmp_path / "search.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    svc = SessionService(SessionStore(tmp_path / "sessions"), EventBus(), tmp_path / "runs")
    yield svc
    index.close()


async def settled(service):
    async with asyncio.timeout(2):
        while service._inflight:
            await asyncio.sleep(0.001)


def fake_result(service, monkeypatch, gate=None):
    async def run(attempt, **kwargs):
        if gate is not None:
            await gate.wait()
        return {"status": "success", "content": "durable answer"}
    monkeypatch.setattr(service, "_run_with_agent", run)


def test_cancel_without_yield_commits_cancel_reply_and_releases(service, monkeypatch):
    async def scenario():
        session = service.create_session()
        fake_result(service, monkeypatch)
        sent = await service.send_message(session.session_id, "first")
        assert service.cancel_current(session.session_id)
        await settled(service)
        attempt = service.store.get_attempt(session.session_id, sent["attempt_id"])
        assert attempt.status == AttemptStatus.CANCELLED
        reply = service.store.get_message_for_attempt(session.session_id, sent["attempt_id"])
        assert reply.metadata["status"] == "cancelled"
        second = await service.send_message(session.session_id, "second")
        assert second["attempt_id"] != sent["attempt_id"]
        await settled(service)
    asyncio.run(scenario())


def test_delayed_conditional_cancel_does_not_cancel_next_attempt(service, monkeypatch):
    async def scenario():
        session = service.create_session()
        fake_result(service, monkeypatch)
        first = await service.send_message(session.session_id, "first")
        await settled(service)
        gate = asyncio.Event()
        fake_result(service, monkeypatch, gate)
        second = await service.send_message(session.session_id, "second")
        assert not service.cancel_current(session.session_id, expected_attempt_id=first["attempt_id"])
        assert service.store.get_attempt(session.session_id, second["attempt_id"]).status in {AttemptStatus.PENDING, AttemptStatus.RUNNING}
        assert service.cancel_current(session.session_id, expected_attempt_id=second["attempt_id"])
        await settled(service)
        assert service.store.get_attempt(session.session_id, second["attempt_id"]).status == AttemptStatus.CANCELLED
    asyncio.run(scenario())


def test_active_delete_is_busy_and_has_no_side_effects(service, monkeypatch):
    async def scenario():
        session = service.create_session()
        gate = asyncio.Event()
        fake_result(service, monkeypatch, gate)
        await service.send_message(session.session_id, "keep")
        try:
            with pytest.raises(SessionBusyError):
                service.delete_session(session.session_id)
            assert service.get_session(session.session_id)
            assert service.event_bus.replay(session.session_id, replay_all=True)
        finally:
            gate.set()
            await settled(service)
    asyncio.run(scenario())


def test_delete_io_error_is_not_success_and_keeps_events(service, monkeypatch):
    session = service.create_session()
    def fail(*args, **kwargs):
        raise OSError("permission denied")
    monkeypatch.setattr("shutil.rmtree", fail)
    with pytest.raises(OSError, match="permission"):
        service.delete_session(session.session_id)
    assert service.get_session(session.session_id)
    assert service.event_bus.replay(session.session_id, replay_all=True)


@pytest.mark.parametrize("phase", ["index", "event", "checkpoint"])
def test_optional_terminal_failure_cannot_rewrite_completed(service, monkeypatch, phase):
    async def scenario():
        session = service.create_session()
        fake_result(service, monkeypatch)
        def fail(*args, **kwargs):
            raise OSError("optional phase unavailable")
        if phase == "index":
            original = service._search_index.index_message
            def index(sid, role, content):
                if role == "assistant":
                    fail()
                original(sid, role, content)
            monkeypatch.setattr(service._search_index, "index_message", index)
        elif phase == "event":
            original = service.event_bus.emit
            def emit(sid, kind, data):
                if kind == "attempt.completed":
                    fail()
                original(sid, kind, data)
            monkeypatch.setattr(service.event_bus, "emit", emit)
        else:
            monkeypatch.setattr(service.store, "delete_partial_response", fail)
        sent = await service.send_message(session.session_id, "finish")
        await settled(service)
        assert service.store.get_attempt(session.session_id, sent["attempt_id"]).status == AttemptStatus.COMPLETED
        assert service.store.get_message_for_attempt(session.session_id, sent["attempt_id"]).content == "durable answer"
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["reply", "attempt"])
def test_terminal_storage_failure_keeps_recoverable_evidence(service, monkeypatch, phase):
    async def scenario():
        session = service.create_session()
        fake_result(service, monkeypatch)
        if phase == "reply":
            original = service.store.append_message
            def append(message):
                if message.role == "assistant":
                    raise OSError("reply write failed")
                original(message)
            monkeypatch.setattr(service.store, "append_message", append)
        else:
            original = service.store.update_attempt
            def update(attempt):
                if attempt.status == AttemptStatus.COMPLETED:
                    raise OSError("attempt write failed")
                original(attempt)
            monkeypatch.setattr(service.store, "update_attempt", update)
        sent = await service.send_message(session.session_id, "finish")
        await settled(service)
        assert service.store.get_attempt(session.session_id, sent["attempt_id"]).status in {AttemptStatus.PENDING, AttemptStatus.RUNNING}
        if phase == "reply":
            assert service.store.get_partial_response(session.session_id, sent["attempt_id"]) == "durable answer"
        else:
            assert service.store.get_message_for_attempt(session.session_id, sent["attempt_id"]).metadata["status"] == "completed"
        monkeypatch.undo()
        monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: service._search_index)
        recovered = SessionService(service.store, EventBus(), service.runs_dir)
        again = SessionService(service.store, EventBus(), service.runs_dir)
        replies = [m for m in again.get_messages(session.session_id) if m.linked_attempt_id == sent["attempt_id"]]
        assert len(replies) == 1
        expected = AttemptStatus.INTERRUPTED if phase == "reply" else AttemptStatus.COMPLETED
        assert recovered.store.get_attempt(session.session_id, sent["attempt_id"]).status == expected
    asyncio.run(scenario())


def test_close_stops_admission_before_any_durable_write(service, monkeypatch):
    async def scenario():
        session = service.create_session()
        summary = await service.aclose(timeout_s=0)
        assert summary.complete
        with pytest.raises(RuntimeError, match="closing|closed"):
            service.create_session()
        with pytest.raises(RuntimeError, match="closing|closed"):
            await service.send_message(session.session_id, "refused")
        assert service.get_messages(session.session_id) == []
        assert len(service.list_sessions()) == 1
        assert (await service.aclose(timeout_s=0)).complete
    asyncio.run(scenario())


def test_idle_service_replacement_retires_old_admission(service):
    replacement = SessionService(service.store, EventBus(), service.runs_dir)
    with pytest.raises(RuntimeError, match="closing|closed"):
        service.create_session()
    assert replacement.create_session()


def install_thread_agent(monkeypatch, started, release, *, registry=False):
    def build(**kwargs):
        if registry:
            started.set()
            assert release.wait(3)
        return object()
    class Agent:
        def __init__(self, **kwargs):
            pass
        def cancel(self):
            pass
        def run(self, **kwargs):
            started.set()
            assert release.wait(3)
            return {"status": "success", "content": "thread answer"}
    monkeypatch.setattr("src.runtime.tools.build_registry", build)
    monkeypatch.setattr("src.runtime.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.runtime.memory.persistent.PersistentMemory", lambda: object())
    monkeypatch.setattr("src.runtime.agent.loop.AgentLoop", Agent)
    monkeypatch.setattr("src.platform.config.loader.load_runtime_agent_config", lambda overrides=None: object())


@pytest.mark.parametrize("registry", [False, True])
def test_cancelled_wrapper_keeps_actual_worker_owner(service, monkeypatch, registry):
    started, release = threading.Event(), threading.Event()
    install_thread_agent(monkeypatch, started, release, registry=registry)
    async def scenario():
        session = service.create_session()
        sent = await service.send_message(session.session_id, "worker")
        try:
            assert await asyncio.to_thread(started.wait, 1)
            wrapper = service._active_tasks[session.session_id]
            wrapper.cancel()
            with pytest.raises(asyncio.CancelledError):
                await wrapper
            with pytest.raises(SessionBusyError):
                await service.send_message(session.session_id, "overlap")
            with pytest.raises(RuntimeError, match="owner|closing|active"):
                SessionService(service.store, EventBus(), service.runs_dir)
            summary = await service.aclose(timeout_s=0)
            assert not summary.complete
            assert sent["attempt_id"] in summary.pending_run_writers
        finally:
            release.set()
            await settled(service)
        assert (await service.aclose(timeout_s=1)).complete
        recovered = SessionService(service.store, EventBus(), service.runs_dir)
        attempt = recovered.store.get_attempt(session.session_id, sent["attempt_id"])
        assert attempt.status == (AttemptStatus.INTERRUPTED if registry else AttemptStatus.COMPLETED)
    asyncio.run(scenario())


@pytest.mark.parametrize("tail", [b'{"bad":', None])
def test_terminal_append_repairs_tail_without_duplicate_or_lost_reply(service, tail):
    session = service.create_session()
    reply = Message(session_id=session.session_id, linked_attempt_id="attempt", role="assistant", content="answer", metadata={"status": "completed"})
    path = service.store._messages_file(session.session_id)
    path.write_bytes(tail if tail is not None else json.dumps(reply.to_dict()).encode())
    assert service.store.get_message_for_attempt(session.session_id, "attempt") is None
    service.store.append_terminal_message(reply)
    service.store.append_terminal_message(reply)
    assert [m.content for m in service.get_messages(session.session_id)] == ["answer"]
    if tail is not None:
        assert path.read_bytes().startswith(tail + b"\n")


def test_deleted_sessions_cannot_leak_from_search_after_projection_failure(service, monkeypatch):
    session = service.create_session()
    service.store.append_message(Message(session_id=session.session_id, content="needle"))
    service._search_index.index_message(session.session_id, "user", "needle")
    assert service._search_index.search("needle")
    # Simulate an old stale projection left after a crash/direct filesystem deletion.
    assert service.store.delete_session(session.session_id)
    assert service._search_index.search("needle") == []


def test_search_delete_updates_fts_transactionally(tmp_path):
    index = SessionSearchIndex(tmp_path / "index.db")
    try:
        index.index_session("one", "one")
        index.index_message("one", "user", "needle")
        conn = index._get_conn()
        conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT, 'fault'); END")
        conn.commit()
        with pytest.raises(Exception, match="fault"):
            index.delete_session("one")
        assert index.search("needle")[0].session_id == "one"
        conn.execute("DROP TRIGGER fail_delete")
        conn.commit()
        index.delete_session("one")
        assert index.search("needle") == []
    finally:
        index.close()


def test_bounded_admission_refuses_before_persisting(tmp_path, monkeypatch):
    index = SessionSearchIndex(tmp_path / "bounded.db")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: index)
    async def scenario():
        service = SessionService(SessionStore(tmp_path / "sessions"), EventBus(), tmp_path / "runs", max_inflight=1)
        first, second = service.create_session(), service.create_session()
        gate = asyncio.Event()
        fake_result(service, monkeypatch, gate)
        await service.send_message(first.session_id, "first")
        try:
            with pytest.raises(RuntimeError, match="admission|capacity"):
                await service.send_message(second.session_id, "refused")
            assert service.get_messages(second.session_id) == []
            assert service.get_session(second.session_id).last_attempt_id is None
        finally:
            gate.set()
            await settled(service)
    try:
        asyncio.run(scenario())
    finally:
        index.close()


def test_rest_active_delete_conditional_cancel_and_closed_admission(service, monkeypatch):
    import httpx
    import src.interfaces.api_server as api_server
    monkeypatch.setattr(api_server, "_get_session_service", lambda: service)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    async def scenario():
        session = service.create_session()
        gate = asyncio.Event()
        fake_result(service, monkeypatch, gate)
        await service.send_message(session.session_id, "first")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app, client=("127.0.0.1", 1)), base_url="http://localhost") as client:
            try:
                response = await client.post(f"/sessions/{session.session_id}/cancel", json={"expected_attempt_id": "old"})
                assert response.json() == {"status": "no_active_loop"}
                response = await client.delete(f"/sessions/{session.session_id}")
                assert response.status_code == 409
                assert isinstance(response.json()["detail"], str)
            finally:
                gate.set()
                await settled(service)
            await service.aclose(timeout_s=1)
            assert (await client.post("/sessions", json={})).status_code == 503
            assert (await client.post(f"/sessions/{session.session_id}/messages", json={"content": "refused"})).status_code == 503
    asyncio.run(scenario())


def test_attempt_snapshot_prefers_committed_reply_and_hides_execution_details(service, monkeypatch):
    import httpx
    import src.interfaces.api_server as api_server
    monkeypatch.setattr(api_server, "_get_session_service", lambda: service)
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "")
    async def scenario():
        session = service.create_session()
        gate = asyncio.Event()
        fake_result(service, monkeypatch, gate)
        sent = await service.send_message(session.session_id, "private prompt")
        try:
            snapshot = service.get_attempt_snapshot(session.session_id, sent["attempt_id"])
            assert snapshot["status"] in {"pending", "running"}
            service.store.append_message(Message(role="assistant", session_id=session.session_id, content="", linked_attempt_id=sent["attempt_id"], metadata={"status": "cancelled", "elapsed_ms": 12, "run_id": "safe-id", "credential": "private", "run_dir": "/private/run"}))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api_server.app, client=("127.0.0.1", 1)), base_url="http://localhost") as client:
                response = await client.get(f"/sessions/{session.session_id}/attempts/{sent['attempt_id']}")
                assert response.status_code == 200
                payload = response.json()
                assert payload["status"] == "cancelled"
                assert payload["terminal_metadata"]["elapsed_ms"] == 12
                assert "prompt" not in payload and "private" not in response.text
                assert (await client.get(f"/sessions/{session.session_id}/attempts/missing")).status_code == 404
        finally:
            gate.set()
            await settled(service)
    asyncio.run(scenario())


def test_delete_and_send_share_one_admission_gate(service, monkeypatch):
    async def scenario():
        session = service.create_session()
        gate = asyncio.Event()
        fake_result(service, monkeypatch, gate)
        reading, delete_entered, deleted = threading.Event(), threading.Event(), threading.Event()
        original = service.store.get_session
        def paused_read(sid):
            result = original(sid)
            reading.set()
            assert delete_entered.wait(1)
            return result
        monkeypatch.setattr(service.store, "get_session", paused_read)
        errors = []
        def delete():
            assert reading.wait(1)
            delete_entered.set()
            try:
                service.delete_session(session.session_id)
            except Exception as exc:
                errors.append(exc)
            finally:
                deleted.set()
        deleter = threading.Thread(target=delete)
        deleter.start()
        try:
            await service.send_message(session.session_id, "admitted")
            assert await asyncio.to_thread(deleted.wait, 1)
            assert len(errors) == 1 and isinstance(errors[0], SessionBusyError)
            assert original(session.session_id)
        finally:
            gate.set()
            await settled(service)
            deleter.join(1)
    asyncio.run(scenario())


def test_old_callbacks_cannot_write_or_release_next_attempt(service, monkeypatch):
    captured = []
    class Agent:
        def __init__(self, **kwargs):
            captured.append(kwargs["event_callback"])
        def run(self, **kwargs):
            return {"status": "success", "content": "first answer"}
    monkeypatch.setattr("src.runtime.tools.build_registry", lambda **kwargs: object())
    monkeypatch.setattr("src.runtime.providers.chat.ChatLLM", lambda: object())
    monkeypatch.setattr("src.runtime.memory.persistent.PersistentMemory", lambda: object())
    monkeypatch.setattr("src.runtime.agent.loop.AgentLoop", Agent)
    monkeypatch.setattr("src.platform.config.loader.load_runtime_agent_config", lambda overrides=None: object())
    async def scenario():
        session = service.create_session()
        first = await service.send_message(session.session_id, "first")
        supervisor = getattr(service, "_supervisor", None)
        older = supervisor.handles[session.session_id] if supervisor is not None else None
        await settled(service)
        gate = asyncio.Event()
        fake_result(service, monkeypatch, gate)
        second = await service.send_message(session.session_id, "second")
        try:
            captured[0]("text_delta", {"delta": "late stale text"})
            assert service.store.get_partial_response(session.session_id, first["attempt_id"]) is None
            assert not service._supervisor.release(older)
            with pytest.raises(SessionBusyError):
                await service.send_message(session.session_id, "overlap")
            assert not service.cancel_current(session.session_id, first["attempt_id"])
        finally:
            gate.set()
            await settled(service)
        assert service.store.get_attempt(session.session_id, second["attempt_id"]).status == AttemptStatus.COMPLETED
    asyncio.run(scenario())


@pytest.mark.parametrize("yield_before_cancel", [False, True])
def test_cancelled_finalizer_does_not_abandon_live_writer(service, monkeypatch, yield_before_cancel):
    started, release = threading.Event(), threading.Event()
    install_thread_agent(monkeypatch, started, release)
    async def scenario():
        session = service.create_session()
        sent = await service.send_message(session.session_id, "worker")
        try:
            assert await asyncio.to_thread(started.wait, 1)
            handle = service._supervisor.handles[session.session_id]
            handle.task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handle.task
            if yield_before_cancel:
                await asyncio.sleep(0)
            handle.finalizer.cancel()
            await asyncio.sleep(0)
            assert service._supervisor.owns(handle)
        finally:
            release.set()
        await settled(service)
        assert service.store.get_attempt(session.session_id, sent["attempt_id"]).status == AttemptStatus.COMPLETED
    asyncio.run(scenario())


def test_uncertain_reply_append_recovers_one_completed_reply(service, monkeypatch):
    async def scenario():
        session = service.create_session()
        fake_result(service, monkeypatch)
        original = service.store.append_message
        def uncertain(message):
            original(message)
            if message.role == "assistant":
                raise OSError("fsync acknowledgement lost")
        monkeypatch.setattr(service.store, "append_message", uncertain)
        sent = await service.send_message(session.session_id, "finish")
        await settled(service)
        assert service.store.get_attempt(session.session_id, sent["attempt_id"]).status == AttemptStatus.RUNNING
        monkeypatch.setattr(service.store, "append_message", original)
        recovered = SessionService(service.store, EventBus(), service.runs_dir)
        replies = [m for m in recovered.get_messages(session.session_id) if m.linked_attempt_id == sent["attempt_id"]]
        assert len(replies) == 1 and replies[0].content == "durable answer"
        assert recovered.store.get_attempt(session.session_id, sent["attempt_id"]).status == AttemptStatus.COMPLETED
    asyncio.run(scenario())


def test_finalization_owns_delete_and_refuses_late_cancel(service, monkeypatch):
    async def scenario():
        session = service.create_session()
        fake_result(service, monkeypatch)
        original = service.store.append_message
        observed = []
        def append(message):
            if message.role == "assistant":
                with pytest.raises(SessionBusyError):
                    service.delete_session(session.session_id)
                observed.append(service.cancel_current(session.session_id))
                summary = service._supervisor.close_summary()
                assert summary.pending_finalizers == (message.linked_attempt_id,)
            original(message)
        monkeypatch.setattr(service.store, "append_message", append)
        sent = await service.send_message(session.session_id, "finish")
        await settled(service)
        assert observed == [False]
        assert service.store.get_attempt(session.session_id, sent["attempt_id"]).status == AttemptStatus.COMPLETED
    asyncio.run(scenario())


def test_recovery_adopts_valid_repaired_tail_instead_of_interrupting(service):
    session = service.create_session()
    attempt = Attempt(session_id=session.session_id, prompt="answer")
    attempt.mark_running()
    service.store.create_attempt(attempt)
    reply = Message(session_id=session.session_id, role="assistant", content="complete answer", linked_attempt_id=attempt.attempt_id, metadata={"status": "completed", "metrics": {"sharpe": 1.0}})
    service.store._messages_file(session.session_id).write_text(json.dumps(reply.to_dict()), encoding="utf-8")
    assert service.store.get_message_for_attempt(session.session_id, attempt.attempt_id) is None
    recovered = SessionService(service.store, EventBus(), service.runs_dir)
    assert recovered.store.get_attempt(session.session_id, attempt.attempt_id).status == AttemptStatus.COMPLETED
    assert recovered.store.get_attempt(session.session_id, attempt.attempt_id).metrics == {"sharpe": 1.0}
    assert [m.content for m in recovered.get_messages(session.session_id)] == ["complete answer"]


def test_terminal_candidate_has_stable_identity_before_transcript_commit(service, monkeypatch):
    session = service.create_session()
    attempt = Attempt(session_id=session.session_id, prompt="answer")
    attempt.mark_running()
    service.store.create_attempt(attempt)
    candidates = []
    def reject(message):
        candidates.append(message.message_id)
        raise OSError("append unavailable")
    monkeypatch.setattr(service.store, "append_message", reject)
    for _ in range(2):
        with pytest.raises(OSError):
            service._persist_outcome(session, attempt, {"status": "success", "content": "answer"}, 1)
    assert len(candidates) == 2 and candidates[0] == candidates[1]
    assert service.get_messages(session.session_id) == []
    assert service.store.get_partial_response(session.session_id, attempt.attempt_id) == "answer"


def test_slow_terminal_io_keeps_loop_live_and_close_reports_finalizer(service, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original = service.store.append_message
    def slow_append(message):
        if message.role == "assistant":
            started.set()
            # Finite safety timeout ensures the RED implementation cannot hang
            # the test process when it blocks the event loop itself.
            assert release.wait(2)
        original(message)
    monkeypatch.setattr(service.store, "append_message", slow_append)
    async def scenario():
        session = service.create_session()
        fake_result(service, monkeypatch)
        sent = await service.send_message(session.session_id, "finish")
        try:
            assert await asyncio.to_thread(started.wait, 1)
            before = asyncio.get_running_loop().time()
            await asyncio.sleep(0)
            summary = await service.aclose(timeout_s=0.01)
            assert asyncio.get_running_loop().time() - before < 0.5
            assert not summary.complete
            assert summary.pending_run_writers == ()
            assert summary.pending_finalizers == (sent["attempt_id"],)
            with pytest.raises(RuntimeError, match="owner|active|closing"):
                SessionService(service.store, EventBus(), service.runs_dir)
        finally:
            release.set()
            await settled(service)
        replies = [m for m in service.get_messages(session.session_id) if m.linked_attempt_id == sent["attempt_id"]]
        assert len(replies) == 1 and replies[0].metadata["status"] == "completed"
        assert (await service.aclose(timeout_s=1)).complete
    asyncio.run(scenario())


def test_concurrent_index_write_cannot_commit_another_failed_delete(tmp_path, monkeypatch):
    index = SessionSearchIndex(tmp_path / "concurrent.db")
    index.index_session("one", "one")
    index.index_message("one", "user", "needle")
    index.index_session("two", "two")
    conn = index._get_conn()
    conn.execute("CREATE TRIGGER fail_delete BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT, 'fault'); END")
    conn.commit()
    deleting, indexed = threading.Event(), threading.Event()
    class ControlledConnection:
        def __enter__(self):
            conn.__enter__()
            return self
        def __exit__(self, *args):
            return conn.__exit__(*args)
        def execute(self, sql, params=()):
            result = conn.execute(sql, params)
            if sql.startswith("DELETE FROM messages"):
                deleting.set()
                indexed.wait(0.2)  # Deadlock safety when the transaction gate works.
            return result
        def commit(self):
            conn.commit()
            indexed.set()
    monkeypatch.setattr(index, "_get_conn", lambda: ControlledConnection())
    errors = []
    def delete():
        try:
            index.delete_session("one")
        except Exception as exc:
            errors.append(exc)
    deleter = threading.Thread(target=delete)
    deleter.start()
    try:
        assert deleting.wait(1)
        index.index_message("two", "user", "other")
        deleter.join(1)
        assert errors and "fault" in str(errors[0])
        assert index.search("needle")[0].session_id == "one"
        assert index.search("other")[0].session_id == "two"
    finally:
        indexed.set()
        deleter.join(1)
        monkeypatch.undo()
        index.close()


@pytest.mark.parametrize("failure_phase", [None, "reply", "attempt"])
def test_same_store_recovery_is_owned_until_writes_or_failure_exit(service, monkeypatch, failure_phase):
    """A second store object cannot retire a constructor which is still writing."""
    session = service.create_session()
    attempt = Attempt(session_id=session.session_id, prompt="recover")
    attempt.mark_running()
    service.store.create_attempt(attempt)
    recovering_store = SessionStore(service.store.base_dir)
    competing_store = SessionStore(service.store.base_dir)
    entered, release = threading.Event(), threading.Event()
    outcomes, failures = [], []
    recovering_owners = []
    recover = SessionService._recover_interrupted_attempts
    def capture_owner(owner):
        recovering_owners.append(owner)
        recover(owner)
    monkeypatch.setattr(SessionService, "_recover_interrupted_attempts", capture_owner)
    if failure_phase == "attempt":
        def blocked_update(record):
            entered.set()
            assert release.wait(2)
            raise OSError("recovery attempt fault")
        monkeypatch.setattr(recovering_store, "update_attempt", blocked_update)
    else:
        original = recovering_store.append_message
        def blocked_append(message):
            entered.set()  # append_terminal_message has already checked idempotency.
            assert release.wait(2)
            if failure_phase == "reply":
                raise OSError("recovery reply fault")
            original(message)
        monkeypatch.setattr(recovering_store, "append_message", blocked_append)
    def construct():
        try:
            outcomes.append(SessionService(recovering_store, EventBus(), service.runs_dir))
        except Exception as exc:
            failures.append(exc)
    constructor = threading.Thread(target=construct)
    constructor.start()
    try:
        assert entered.wait(1)
        if failure_phase is None:
            summary = asyncio.run(recovering_owners[0].aclose(timeout_s=0))
            assert not summary.complete
            assert recovering_owners[0]._supervisor.state.value == "closing"
        with pytest.raises(SessionServiceClosingError, match="owner|initializ|recover|active"):
            SessionService(competing_store, EventBus(), service.runs_dir)
    finally:
        release.set()
        constructor.join(2)
    assert not constructor.is_alive()
    if failure_phase is None:
        assert not failures and len(outcomes) == 1
    else:
        assert not outcomes and len(failures) == 1 and isinstance(failures[0], OSError)
    # The initializing owner is released only after its stack exited; errors do
    # not leave a permanent admission/recovery lock on this store.
    recovered = SessionService(competing_store, EventBus(), service.runs_dir)
    replies = [m for m in recovered.get_messages(session.session_id) if m.linked_attempt_id == attempt.attempt_id]
    assert len(replies) == 1
    assert recovered.store.get_attempt(session.session_id, attempt.attempt_id).status == AttemptStatus.INTERRUPTED
    assert recovered.create_session(title="usable after recovery")


def test_initialization_resource_failure_releases_owner_after_actual_exit(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    failures = []
    def blocked_index():
        entered.set()
        assert release.wait(2)
        raise OSError("index initialization fault")
    monkeypatch.setattr("src.runtime.session.service.get_shared_index", blocked_index)
    def construct():
        try:
            SessionService(SessionStore(service.store.base_dir), EventBus(), service.runs_dir)
        except Exception as exc:
            failures.append(exc)
    constructor = threading.Thread(target=construct)
    constructor.start()
    try:
        assert entered.wait(1)
        # A half-initialized owner is also live before recovery starts.
        with pytest.raises(SessionServiceClosingError):
            SessionService(SessionStore(service.store.base_dir), EventBus(), service.runs_dir)
    finally:
        release.set()
        constructor.join(2)
        monkeypatch.setattr("src.runtime.session.service.get_shared_index", lambda: service._search_index)
    assert not constructor.is_alive()
    assert len(failures) == 1 and isinstance(failures[0], OSError)
    replacement = SessionService(SessionStore(service.store.base_dir), EventBus(), service.runs_dir)
    assert replacement.create_session()
