> Archived revision-specific task evidence. Original status and pending notes below
> refer to that recorded revision. Final closure and integration results are in the
> [delivery report](../2026-10-14-stability-verification.md). Temporary diff packages
> and local logs were review inputs; patch ranges can be recreated from recorded commits.

# Task 2 independent review

## Spec Compliance

- ❌ Issues found: synchronous lazy construction does not establish the actual execution loop before close (`agent/src/interfaces/api/runtime.py:51`, `:85`, `:98`); cross-loop callback ingress remains unbounded (`agent/src/runtime/session/events.py:153`). Both violate explicit Task 2 requirements. Reviewed BASE `1c864c9972fac0d21ec6871e10df557017e9bcf2` → HEAD `56da74615866cbf91533c9a0fba665ea8f20ac94`.
- ⚠️ Cannot verify from this diff: Task 4/5 process-scoped abandoned-reader/producer aggregation. Current RuntimeServices passes through Task 1's zero counts; root must complete that planned handoff before integration acceptance. This is not a Task 2 defect by itself (`agent/src/interfaces/api/runtime.py:98`).
- ⚠️ Cannot verify from this diff: Task 3 consumes exact no-id `session.resync` payload `{reason: queue_overflow|cursor_expired, latest_event_id: string|null}`, hydrates authoritative state and replaces the damaged connection without resending mutations. Backend payload/fencing implementation is present (`agent/src/runtime/session/events.py:73`, `:212`).
- ⚠️ Cannot verify independently: complete integration gate and raw command transcripts. Implementer reports 15 core PASS; earlier relevant run 410 PASS/1 skip/9 existing NumPy warnings; wider run 9 failures, with 3 socket failures rerun PASS and exact remaining 6 IDs failing identically on BASE. Controller reports prior complete baseline 17,890 PASS/85 skip/2,097 existing warnings. These scoped/environment limitations remain recorded; none establishes that the two newly reproduced cases pass. No broad suite was rerun.

## Strengths

- Actual worker and finalizer resource retention is exercised using real executor futures and controlled thread Events, rather than relying only on a mocked complete flag (`agent/tests/test_api_runtime_lifecycle.py:74`). Idle completion releases bus/executor resources once and a later lifespan creates new service instances (`agent/tests/test_api_runtime_lifecycle.py:226`).
- App lifecycle cleanup is in nested finally blocks, so preflight or inbound-shutdown failure still reaches session close (`agent/src/interfaces/api_server.py:151`). Focused inspection of unchanged `agent/src/interfaces/api/scheduled_routes.py:184` confirmed live drivers → channels → scheduled executor shutdown is itself nested with finally blocks.
- Subscriber state captures its actual running loop; replay snapshot and registration share the publish lock. Resync has no SSE id and fences ordinary events; close/resync/terminal slots do not depend on the 200-slot data queue (`agent/src/runtime/session/events.py:60`, `:73`, `:212`). Exact 200/201 pressure and transport EOF versus session-clear behavior have focused tests (`agent/tests/test_event_bus_backpressure.py:21`, `:42`; `agent/src/runtime/session/events.py:265`).
- Changes stay within API composition, session transport and focused tests, preserving domain/API separation and adding no production dependency. Public SSE event names/terminal payloads remain intact; the additional control is explicitly reported for Task 3.

## Critical (Must Fix)

- None found.

## Important (Should Fix)

1. **Bind ownership to the loop that executes session work, not the first close caller.** `agent/src/interfaces/api/runtime.py:51` leaves `_owner_loop` unset after supported synchronous lazy construction. At `:85`, the first `aclose()` arbitrarily assigns its caller's loop, so `:98` invokes `SessionService.aclose()` even when real session tasks belong to another running loop. The unchanged service cancels `handle.task` at `agent/src/runtime/session/service.py:331`. A focused check created RuntimeServices/SessionService synchronously, started a real supervised worker on a debug owner loop in another thread, then called `asyncio.run(runtime.aclose(0))`: it raised `RuntimeError: Non-thread-safe operation invoked on an event loop other than the current one`. After the real worker was released, owner-loop cleanup still reported incomplete and the wrapper remained pending, showing this can strand the execution owner. The existing cross-loop regression constructs the service inside its owner loop, so it misses this supported path (`agent/tests/test_api_runtime_lifecycle.py:144`). Establish/check execution ownership before calling async close, or conservatively use only thread-safe supervisor snapshots whenever task ownership is unproven; never cancel foreign-loop tasks. Add a regression for synchronous lazy creation followed by owner-loop execution and foreign-loop close, requiring eventual true finalization and rebuild.

2. **Bound the publisher-to-loop mailbox as well as the asyncio data queue.** `agent/src/runtime/session/events.py:153` schedules a separate `call_soon_threadsafe(sub.deliver, event, cursor)` for every published event. Queue overflow is detected only when these callbacks eventually execute. A stalled/slower subscriber loop therefore retains arbitrarily many callback handles, events and payloads before the 200-item queue, including after the subscriber has already become gapped. A focused 2,000-event burst without yielding produced **2,000 pending delivery callbacks while the bounded data queue contained 0 events**; after yielding it emitted resync, too late to bound ingress memory. This violates the specified finite-memory backpressure guarantee and can also delay terminal/close callbacks behind the entire backlog. Use a bounded, locked ingress mailbox with independent control state and at most one scheduled drain per subscriber (or equivalent bounded handoff), while keeping actual asyncio queue mutation on its owner loop. Add a blocked-loop burst regression asserting bounded scheduled callbacks/pending ingress and eventual resync/terminal/close delivery.

## Minor (Nice to Have)

- No new task-attributable minor defect established. Reported warning output is not pristine: the focused compatibility run retained nine existing `ccxt_loader.py:39–47` NumPy deprecations, and the controller's baseline has existing warnings. Preserve this noise as a known integration limitation rather than claiming warning-free broad validation.

## Checks and scope

- Read task brief, binding design, implementation report and the supplied six-file review diff. The first combined tool output truncated production hunks; only the omitted diff range was retrieved to complete that read. No changed production/test file was reread independently; a later diff parse extracted line-number metadata only.
- Named cross-layer risk: wrong-loop shutdown cancellation. Focused unchanged inspection covered `SessionService.send_message/aclose` and `SessionExecutionSupervisor.begin_close/close_summary/release`, confirming where actual tasks are created/cancelled and why a pending wrapper retains ownership. The single isolated reproduction used a temporary store/SQLite index, real executor work and no external services; its pending-task warning is part of the reproduced failure, not a pre-existing warning.
- Named cross-layer risk: shutdown ordering after startup/inbound exceptions. Focused unchanged inspection of `scheduled_routes._stop_scheduled_research_on_shutdown` confirmed all inbound stop attempts precede runtime close even on errors.
- Named memory-bound doubt: callback ingress before subscriber queue. Ran one isolated in-memory burst check; observed 2,000 pending callbacks for 2,000 publications, queue size 0 before delivery, followed by `session.resync`.
- No suite rerun, remote operation, dependency installation, credential access, production/test edit, index/HEAD mutation or subagent dispatch. Only this permitted scratch report was written.

## Assessment

**Task quality: Needs fixes.**

The implementation has useful real-resource lifecycle tests and explicit gap semantics, but the uncovered lazy-construction path can strand an owner through foreign-loop cancellation, and callback ingress defeats the promised bounded SSE memory. Both are concrete regressions/requirements gaps requiring correction before Task 2 approval.

## Focused reproduction commands and fix acceptance

Command 1 (run once; output: 2,000 publications → 2,000 pending `deliver` callbacks, queue size 0; next observed frame `session.resync`):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=agent .venv/bin/python - <<'PY'
import asyncio
from src.runtime.session.events import EventBus
async def main():
    bus = EventBus()
    seed = bus.emit('s', 'text_delta', {})
    stream = bus.subscribe('s', replay_all=True)
    assert await anext(stream) is seed
    loop = asyncio.get_running_loop()
    for n in range(2000):
        bus.emit('s', 'text_delta', {'n': n})
    pending = sum(getattr(h._callback, '__name__', '') == 'deliver' for h in loop._ready)
    print({'events_published': 2000, 'pending_delivery_callbacks': pending,
           'bounded_data_queue_size_before_callbacks': bus._subscribers['s'][0].queue.qsize()})
    await asyncio.sleep(0)
    print({'after_delivery': (await anext(stream)).event_type})
    await stream.aclose()
    bus.close()
asyncio.run(main())
PY
```

Command 2 (run once; reproduces wrong-loop cancellation plus stranded pending wrapper; all state is temporary):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=agent .venv/bin/python - <<'PY'
import asyncio
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from src.interfaces.api.runtime import RuntimeServices
from src.runtime.session.search import SessionSearchIndex
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    index = SessionSearchIndex(root / 'index.db')
    with patch('src.runtime.session.service.get_shared_index', lambda: index):
        runtime = RuntimeServices(root / 'sessions', root / 'runs')
        service = runtime.get_session_service()
        loop = asyncio.new_event_loop()
        loop.set_debug(True)
        ready, release = threading.Event(), threading.Event()
        async def run(attempt, **kwargs):
            def worker():
                ready.set()
                release.wait(3)
                return {'status': 'success', 'content': 'done'}
            handle = service._supervisor.handles[attempt.session_id]
            future = service._supervisor.submit(handle, worker, main=True)
            return await asyncio.shield(asyncio.wrap_future(future))
        service._run_with_agent = run
        async def begin():
            session = service.create_session()
            await service.send_message(session.session_id, 'test')
        def owner():
            asyncio.set_event_loop(loop)
            loop.run_until_complete(begin())
            loop.run_forever()
        thread = threading.Thread(target=owner)
        thread.start()
        try:
            assert ready.wait(2)
            try:
                print('foreign_close_result', asyncio.run(runtime.aclose(0)))
            except Exception as exc:
                print('foreign_close_exception', type(exc).__name__, str(exc))
        finally:
            release.set()
            print('owner_cleanup', asyncio.run_coroutine_threadsafe(service.aclose(1), loop).result(2))
            print('runtime_cleanup', asyncio.run(runtime.aclose(0)))
            loop.call_soon_threadsafe(loop.stop)
            thread.join(2)
            loop.close()
            index.close()
PY
```

Observed command 2: `foreign_close_exception RuntimeError Non-thread-safe operation invoked on an event loop other than the current one`; both cleanup summaries `complete=False`, one `pending_run_writers`, zero pending finalizers; `Task was destroyed but it is pending!` identifies `SessionService._run_attempt` waiting on a cancelled Future. This is evidence of the bug, not a request to keep rerunning it.

- Fix acceptance for both publication contexts: same-loop rapid publishing and worker-thread publishing use the same `_schedule_locked` ingress today; a fix must bound both without losing publish/replay ordering. At most one pending drain is insufficient if that drain's mailbox is itself unbounded.
- Fix acceptance for wakeups: current `take()` → `wake.clear()` is serialized on the subscriber loop with no await in between, so no separate lost-wakeup defect was established. A new thread-safe mailbox must atomically coordinate enqueue, draining and clearing the scheduled flag so a publisher arriving during drain completion cannot strand a terminal/close event. Test that handoff with a deterministic barrier, including post-gap suppression and terminal coalescing.
- Fix acceptance for lifecycle: an owner loop must reflect execution admission, not constructor timing or the first close caller. Cover synchronous creation, creation in a different idle loop, active work in its true loop, and foreign-loop observation without awaiting/cancelling that loop's tasks. A temporary foreign-loop close must not prevent the genuine owner loop from completing finalization.
