"""Behaviour tests for the run-stall watchdog.

The watchdog requests cooperative stop. It must leave terminal persistence to
the main owner, which may still be executing a write.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.runtime.agent.loop import AgentLoop
from src.runtime.agent.tools import ToolRegistry
from src.runtime.agent.trace import TraceWriter
from src.platform.core.state import RunStateStore


class _FastEvent(threading.Event):
    """An Event whose ``wait`` never actually sleeps.

    The watchdog polls on a five-second floor, so a real wait would make
    every test in this file take ten seconds to observe two iterations.
    Collapsing the sleep keeps the watchdog's own logic under test.
    """

    def wait(self, timeout: float | None = None) -> bool:
        return super().wait(0.01)


@pytest.fixture()
def loop_and_run_dir(tmp_path: Path) -> tuple[AgentLoop, Path]:
    """Build an AgentLoop with a real run directory and a fast done-event."""
    agent = AgentLoop(registry=ToolRegistry(), llm=SimpleNamespace(), max_iterations=1)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    agent.memory.run_dir = str(run_dir)
    agent._run_done = _FastEvent()
    return agent, run_dir


def _events(agent: AgentLoop) -> list[tuple[str, dict]]:
    """Attach a capturing event callback and return the list it fills."""
    captured: list[tuple[str, dict]] = []
    agent._event_callback = lambda name, data: captured.append((name, data))
    return captured


def test_watchdog_fails_a_run_with_no_forward_progress(
    loop_and_run_dir: tuple[AgentLoop, Path],
) -> None:
    """No true completion requests failure without ending the owner."""
    agent, run_dir = loop_and_run_dir
    captured = _events(agent)
    agent._lifecycle._progress_at = -10_000
    trace = TraceWriter(run_dir)

    agent._stall_watchdog(trace, run_dir, RunStateStore(), stall_timeout=1.0)
    trace.close()

    names = [name for name, _ in captured]
    assert names == ["stall_warning", "stalled"]
    assert agent._stall_reason is not None
    assert "stall watchdog" in agent._stall_reason
    # The loop must exit at its next boundary rather than stay "running".
    assert agent._cancel_event.is_set()

    assert not (run_dir / "state.json").exists()
    assert not [rec for rec in TraceWriter.read(run_dir) if rec.get("type") == "end"]


def test_forward_progress_clears_the_pending_warning(
    loop_and_run_dir: tuple[AgentLoop, Path],
) -> None:
    """A warned-but-recovered run must warn afresh, never fail on the stale flag.

    Guards the ``warned = False`` reset: without it, one warning plus any
    later idle poll would fail a run that had made progress in between.
    """
    agent, run_dir = loop_and_run_dir
    captured = _events(agent)
    trace = TraceWriter(run_dir)
    agent._lifecycle._progress_at = -10_000

    polls = {"n": 0}
    original_wait = agent._run_done.wait

    def _wait(timeout: float | None = None) -> bool:
        # Poll 1: idle -> warn. Poll 2: progress just landed -> reset.
        # Poll 3: idle again -> must warn a second time, not fail.
        polls["n"] += 1
        if polls["n"] == 2:
            agent._lifecycle.progress()
        elif polls["n"] == 3:
            agent._lifecycle._progress_at = -10_000
        elif polls["n"] >= 4:
            agent._run_done.set()
        return original_wait(timeout)

    agent._run_done.wait = _wait  # type: ignore[method-assign]
    agent._stall_watchdog(trace, run_dir, RunStateStore(), stall_timeout=1.0)
    trace.close()

    assert [name for name, _ in captured] == ["stall_warning", "stall_warning"]
    assert agent._stall_reason is None
    assert not agent._cancel_event.is_set()
    assert not (run_dir / "state.json").exists()


def test_watchdog_exits_when_the_run_completes(
    loop_and_run_dir: tuple[AgentLoop, Path],
) -> None:
    """A finished run must not be touched, however long it was idle."""
    agent, run_dir = loop_and_run_dir
    captured = _events(agent)
    agent._lifecycle._progress_at = -10_000
    agent._run_done.set()
    trace = TraceWriter(run_dir)

    agent._stall_watchdog(trace, run_dir, RunStateStore(), stall_timeout=1.0)
    trace.close()

    assert captured == []
    assert agent._stall_reason is None
    assert not agent._cancel_event.is_set()
    assert not (run_dir / "state.json").exists()


def test_watchdog_of_a_finished_run_does_not_fail_the_next_one(
    loop_and_run_dir: tuple[AgentLoop, Path],
) -> None:
    """A reused AgentLoop must not let run N's watchdog kill run N+1.

    The thread captures its done-event locally, so replacing
    ``self._run_done`` for the next run leaves the old watchdog watching the
    old event -- which is already set.
    """
    agent, run_dir = loop_and_run_dir
    captured = _events(agent)
    first_run_done = agent._run_done
    agent._lifecycle._progress_at = -10_000
    trace = TraceWriter(run_dir)

    original_wait = first_run_done.wait
    polls = {"n": 0}

    def _wait(timeout: float | None = None) -> bool:
        # Poll 1: run 2 has started and installed its own event, but run 1's
        # event is not set yet -- the window in which watching
        # ``self._run_done`` instead of the captured local would hand run 1's
        # idle clock to run 2 and fail it.
        polls["n"] += 1
        if polls["n"] == 1:
            agent._run_done = _FastEvent()
        else:
            first_run_done.set()
        return original_wait(timeout)

    first_run_done.wait = _wait  # type: ignore[method-assign]
    agent._stall_watchdog(trace, run_dir, RunStateStore(), stall_timeout=1.0)
    trace.close()

    # Obsolete ownership cannot emit a warning or request failure.
    assert [name for name, _ in captured] == []
    assert agent._stall_reason is None
    assert not agent._cancel_event.is_set()
    assert not (run_dir / "state.json").exists()


def test_stall_timeout_env_alias_resolves_and_zero_disables() -> None:
    """The documented off switch must be reachable by its real env name."""
    from src.platform.config.env_schema import AgentTuningConfig

    assert AgentTuningConfig().vibe_trading_run_stall_timeout_seconds == 1800.0
    disabled = AgentTuningConfig(VIBE_TRADING_RUN_STALL_TIMEOUT_SECONDS="0")
    assert disabled.vibe_trading_run_stall_timeout_seconds == 0.0
    tightened = AgentTuningConfig(VIBE_TRADING_RUN_STALL_TIMEOUT_SECONDS="600")
    assert tightened.vibe_trading_run_stall_timeout_seconds == 600.0
