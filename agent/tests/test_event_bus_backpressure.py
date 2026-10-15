"""SSE gaps and shutdown controls have independent, loop-owned delivery."""

from __future__ import annotations

import asyncio
import threading

import pytest

from src.runtime.session.events import EventBus


async def subscribed(bus):
    first = bus.emit("s", "text_delta", {"delta": "first"})
    stream = bus.subscribe("s", replay_all=True)
    assert await anext(stream) is first
    return stream


@pytest.mark.parametrize("count", [200, 201])
def test_full_queue_close_terminates_stream(count):
    async def scenario():
        bus = EventBus()
        stream = await subscribed(bus)
        for _ in range(count):
            bus.emit("s", "text_delta", {"delta": "x"})
        bus.clear("s")
        await asyncio.sleep(0)
        events = []
        for _ in range(205):
            try:
                events.append(await asyncio.wait_for(anext(stream), 0.2))
            except StopAsyncIteration:
                break
        assert events[-1].event_type == "session_cleared"
        assert not bus._subscribers.get("s")

    asyncio.run(scenario())


@pytest.mark.parametrize("count", [200, 201])
def test_overflow_resync_fences_deltas_and_preserves_terminal(count):
    async def scenario():
        bus = EventBus()
        stream = await subscribed(bus)
        for _ in range(count):
            bus.emit("s", "text_delta", {"delta": "x"})
        terminal = bus.emit("s", "attempt.completed", {"attempt_id": "a", "summary": "done"})
        await asyncio.sleep(0)
        gap = await asyncio.wait_for(anext(stream), 0.2)
        assert gap.event_type == "session.resync"
        assert gap.event_id is None
        assert gap.data == {"reason": "queue_overflow", "latest_event_id": terminal.event_id}
        assert await asyncio.wait_for(anext(stream), 0.2) is terminal
        bus.emit("s", "text_delta", {"delta": "suppressed"})
        bus.clear("s")
        assert (await asyncio.wait_for(anext(stream), 0.2)).event_type == "session_cleared"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


def test_expired_cursor_resyncs_instead_of_incomplete_replay():
    async def scenario():
        bus = EventBus(max_buffer_size=2)
        old = bus.emit("s", "text_delta", {})
        bus.emit("s", "text_delta", {})
        latest = bus.emit("s", "text_delta", {})
        stream = bus.subscribe("s", old.event_id, replay_all=True)
        gap = await asyncio.wait_for(anext(stream), 0.2)
        assert gap.event_type == "session.resync"
        assert gap.data == {"reason": "cursor_expired", "latest_event_id": latest.event_id}
        bus.emit("s", "text_delta", {})
        terminal = bus.emit("s", "attempt.failed", {"attempt_id": "a"})
        assert await asyncio.wait_for(anext(stream), 0.2) is terminal
        await stream.aclose()

    asyncio.run(scenario())


def test_publish_mutates_subscriber_queue_on_its_actual_loop(monkeypatch):
    async def scenario():
        bus = EventBus()
        # An injected obsolete loop must not determine subscriber ownership.
        other = asyncio.new_event_loop()
        bus.set_loop(other)
        stream = await subscribed(bus)
        owner = threading.get_ident()
        threads = []
        original = asyncio.Queue.put_nowait

        def recording_put(queue, event):
            threads.append(threading.get_ident())
            return original(queue, event)

        monkeypatch.setattr(asyncio.Queue, "put_nowait", recording_put)
        terminal = await asyncio.to_thread(bus.emit, "s", "attempt.completed", {})
        assert await asyncio.wait_for(anext(stream), 0.2) is terminal
        assert threads and set(threads) == {owner}
        await stream.aclose()
        other.close()

    asyncio.run(scenario())


def test_bus_close_terminates_idle_subscribers_and_rejects_new_streams():
    async def scenario():
        bus = EventBus()
        stream = await subscribed(bus)
        bus.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(anext(stream), 0.2)
        new_stream = bus.subscribe("s")
        with pytest.raises(StopAsyncIteration):
            await anext(new_stream)

    asyncio.run(scenario())


def test_queued_terminal_survives_later_delta_overflow():
    async def scenario():
        bus = EventBus()
        stream = await subscribed(bus)
        terminal = bus.emit("s", "attempt.completed", {"attempt_id": "a"})
        for _ in range(201):
            latest = bus.emit("s", "text_delta", {})
        await asyncio.sleep(0)
        gap = await anext(stream)
        assert gap.event_type == "session.resync"
        assert gap.data["latest_event_id"] == latest.event_id
        assert await asyncio.wait_for(anext(stream), 0.2) is terminal
        await stream.aclose()

    asyncio.run(scenario())


def test_runtime_stream_close_drains_queued_terminal_without_deletion_signal():
    async def scenario():
        bus = EventBus()
        stream = await subscribed(bus)
        for _ in range(199):
            bus.emit("s", "text_delta", {})
        terminal = bus.emit("s", "attempt.completed", {"attempt_id": "a"})
        bus.close()
        await asyncio.sleep(0)
        received = [event async for event in stream]
        assert terminal in received
        assert all(event.event_type != "session_cleared" for event in received)

    asyncio.run(scenario())


@pytest.mark.parametrize("publisher", ["same_loop", "worker_thread"])
def test_flood_has_bounded_ingress_and_one_scheduled_drain(monkeypatch, publisher):
    async def scenario():
        bus = EventBus()
        stream = await subscribed(bus)
        loop = asyncio.get_running_loop()
        scheduled = []
        real_schedule = loop.call_soon_threadsafe

        def count_schedule(callback, *args, **kwargs):
            scheduled.append(callback)
            return real_schedule(callback, *args, **kwargs)

        monkeypatch.setattr(loop, "call_soon_threadsafe", count_schedule)

        def flood():
            for n in range(2000):
                bus.emit("s", "text_delta", {"n": n})
            terminal = bus.emit("s", "attempt.completed", {"attempt_id": "a"})
            bus.clear("s")
            return terminal

        if publisher == "same_loop":
            terminal = flood()
        else:
            holder = []
            thread = threading.Thread(target=lambda: holder.append(flood()))
            thread.start()
            thread.join(2)  # Intentionally block owner delivery throughout burst.
            assert not thread.is_alive()
            terminal = holder[0]
        assert len(scheduled) <= 1
        sub = scheduled[0].__self__
        assert len(sub.ingress) <= 200
        assert sub.queue.qsize() == 0
        assert (await asyncio.wait_for(anext(stream), 0.2)).event_type == "session.resync"
        assert await asyncio.wait_for(anext(stream), 0.2) is terminal
        assert (await asyncio.wait_for(anext(stream), 0.2)).event_type == "session_cleared"
        with pytest.raises(StopAsyncIteration):
            await anext(stream)

    asyncio.run(scenario())


@pytest.mark.parametrize("handoff", ["before_reset", "after_reset"])
def test_concurrent_enqueue_at_drain_handoff_has_no_lost_terminal_or_close(monkeypatch, handoff):
    async def scenario():
        bus = EventBus()
        stream = await subscribed(bus)
        sub = bus._subscribers["s"][0]
        entered, published = threading.Event(), threading.Event()
        finish = type(sub)._finish_drain
        first_finish = True

        def paused_finish(current):
            nonlocal first_finish
            if current is not sub or not first_finish:
                return finish(current)
            first_finish = False
            if handoff == "after_reset":
                finish(current)
            entered.set()
            assert published.wait(1)
            if handoff == "before_reset":
                finish(current)

        monkeypatch.setattr(type(sub), "_finish_drain", paused_finish)
        for _ in range(2000):
            bus.emit("s", "text_delta", {})
        bus.emit("s", "attempt.completed", {"attempt_id": "old"})
        holder = []

        def publish_at_handoff():
            assert entered.wait(1)
            try:
                for _ in range(2000):
                    bus.emit("s", "text_delta", {"suppressed": True})
                bus.emit("s", "attempt.failed", {"attempt_id": "middle"})
                holder.append(bus.emit("s", "attempt.cancelled", {"attempt_id": "latest"}))
                bus.clear("s")
            finally:
                published.set()

        thread = threading.Thread(target=publish_at_handoff)
        thread.start()
        try:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            thread.join(1)
            assert not thread.is_alive()
            assert len(sub.ingress) == 0
            gap = await asyncio.wait_for(anext(stream), 0.2)
            assert gap.event_type == "session.resync"
            assert gap.data["latest_event_id"] == holder[0].event_id
            assert await asyncio.wait_for(anext(stream), 0.2) is holder[0]
            assert (await asyncio.wait_for(anext(stream), 0.2)).event_type == "session_cleared"
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
        finally:
            published.set()
            thread.join(1)
            await stream.aclose()

    asyncio.run(scenario())
