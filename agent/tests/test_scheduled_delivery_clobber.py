"""A schedule shorter than delivery latency must not clobber the outbox row.

Dispatch returns once a run is *accepted*, not once it is delivered
(#942 / test_scheduled_delivery_outbox.py). is_due() only checked job.status
and next_run_at, so a job whose schedule interval is shorter than the time
its briefing actually takes to generate and send could re-fire while the
previous firing's outbox row was still PENDING or SENDING. _run_job then
unconditionally overwrote job.delivery with the new firing's DeliveryRecord,
and the previous session_id was gone from the record for good: sweep_
deliveries only ever reads the current job.delivery.session_id, so that
briefing was silently dropped with no error anywhere.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.research.scheduled_research.executor import ScheduledResearchExecutor
from src.research.scheduled_research.models import DeliveryStatus, ScheduledResearchJob
from src.research.scheduled_research.store import ScheduledResearchJobStore


def _store(tmp_path: Path) -> ScheduledResearchJobStore:
    return ScheduledResearchJobStore(tmp_path / "jobs.json")


def _job(**overrides) -> ScheduledResearchJob:
    base = dict(id="j1", prompt="brief me", schedule="1000", next_run_at=0, delivery_channel="telegram")
    base.update(overrides)
    return ScheduledResearchJob(**base)


def test_a_re_fire_does_not_clobber_a_still_pending_delivery(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_job())

    sessions = iter(["sess-1", "sess-2"])

    async def dispatch(job):
        return next(sessions)

    # sess-1's run never finishes within this test: the reader always
    # reports it as still in flight, exactly like a briefing that takes
    # longer to generate than the job's own re-fire interval.
    executor = ScheduledResearchExecutor(
        store,
        dispatch,
        now_fn=lambda: 0,
        enabled=False,
        briefing_reader=lambda _s: None,
        channel_sender=None,
        max_consecutive_failures=3,
    )

    asyncio.run(executor.tick(now_ms=1_000))
    first = store.get("j1").delivery
    assert first.status is DeliveryStatus.PENDING
    assert first.session_id == "sess-1"

    # The schedule says the job is due again before sess-1's briefing has
    # been delivered (or even finished running).
    asyncio.run(executor.tick(now_ms=2_000))
    second = store.get("j1").delivery

    assert second.session_id == "sess-1", (
        "a re-fire clobbered the outbox row for a delivery that was still "
        f"PENDING, replacing it with {second.session_id!r} and orphaning "
        "sess-1's briefing forever"
    )


def test_a_re_fire_is_allowed_once_the_previous_delivery_is_terminal(tmp_path: Path) -> None:
    """The gate must not stall a job forever: SENT clears it for the next firing."""
    store = _store(tmp_path)
    store.upsert(_job())

    sessions = iter(["sess-1", "sess-2"])

    async def dispatch(job):
        return next(sessions)

    executor = ScheduledResearchExecutor(
        store,
        dispatch,
        now_fn=lambda: 0,
        enabled=False,
        briefing_reader=lambda _s: ("completed", "text"),
        channel_sender=lambda *_a: asyncio.sleep(0),
        max_consecutive_failures=3,
    )

    asyncio.run(executor.tick(now_ms=1_000))
    assert store.get("j1").delivery.status is DeliveryStatus.SENT

    asyncio.run(executor.tick(now_ms=2_000))
    assert store.get("j1").delivery.session_id == "sess-2"
