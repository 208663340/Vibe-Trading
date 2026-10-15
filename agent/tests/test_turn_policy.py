"""Decisions for one agent turn stay pure and match the loop's previous thresholds."""

from types import SimpleNamespace

from src.runtime.agent.context_budget import MIN_CONVERSATION_TOKENS
from src.runtime.agent.turn_policy import (
    learned_context_window,
    needs_collapse,
    needs_legacy_collapse,
    needs_legacy_micro,
    needs_legacy_summary,
    needs_microcompact,
    needs_summary,
    pending_write_due,
    plan_stream_retry,
    static_context_error,
    static_context_exceeds_budget,
    suppress_tools,
    wrap_up_due,
    wrap_up_message,
)


def _budget(**overrides):
    values = {
        "static_tokens": 100,
        "total": 1_000,
        "micro_at": 800,
        "collapse_at": 900,
        "compact_at": 1_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_static_overflow_names_the_budget() -> None:
    budget = _budget(static_tokens=1_000, total=1_000)
    assert static_context_exceeds_budget(budget)
    message = static_context_error(budget)
    assert "1000 tokens" in message
    assert "1000" in message


def test_compaction_layers_follow_remeasured_tokens() -> None:
    budget = _budget()
    assert needs_microcompact(801, budget)
    assert not needs_microcompact(800, budget)
    assert needs_collapse(901, budget)
    assert not needs_collapse(800, budget)
    assert needs_summary(1_001, budget, force=False, compacted_to_tokens=None)
    assert not needs_summary(1_000, budget, force=False, compacted_to_tokens=None)
    assert needs_summary(10, budget, force=True, compacted_to_tokens=10)
    assert not needs_summary(
        1_001,
        budget,
        force=False,
        compacted_to_tokens=1_001 - MIN_CONVERSATION_TOKENS,
    )


def test_legacy_threshold_fractions() -> None:
    assert needs_legacy_micro(51, 100)
    assert not needs_legacy_micro(50, 100)
    assert needs_legacy_collapse(71, 100)
    assert not needs_legacy_collapse(70, 100)
    assert needs_legacy_summary(101, 100)
    assert not needs_legacy_summary(100, 100)


def test_learned_window_prefers_the_provider_number() -> None:
    assert learned_context_window(12_000, 20_000) == 12_000
    assert learned_context_window(None, 20_000) == 18_000
    assert learned_context_window(0, 20_000) == 18_000


def test_tool_and_wrap_up_gates() -> None:
    assert suppress_tools(8, 8, False)
    assert suppress_tools(2, 8, True)
    assert not suppress_tools(2, 8, False)
    assert wrap_up_due(4, 4, 8)
    assert not wrap_up_due(1, 1, 8)
    assert not wrap_up_due(8, 8, 8)
    assert pending_write_due(7, 8)
    assert not pending_write_due(8, 8)
    text = wrap_up_message(3, 8, "write notes.md")
    assert "3 iterations remaining out of 8" in text
    assert text.endswith("write notes.md")
    assert "\n\n" not in wrap_up_message(3, 8, "")


def test_stream_retry_plan_keeps_overflow_off_the_streak() -> None:
    refused = plan_stream_retry(
        retryable=False,
        context_overflow=False,
        streak=2,
        retry_after_s=None,
        max_delay_s=30,
        backoff_for_streak=lambda streak: float(streak),
    )
    assert refused is None

    overflow = plan_stream_retry(
        retryable=False,
        context_overflow=True,
        streak=2,
        retry_after_s=9,
        max_delay_s=30,
        backoff_for_streak=lambda streak: float(streak),
    )
    assert overflow is not None
    assert overflow.reason == "context_overflow"
    assert overflow.delay_s == 0.0
    assert overflow.next_streak == 2

    capped = plan_stream_retry(
        retryable=True,
        context_overflow=False,
        streak=1,
        retry_after_s=90,
        max_delay_s=30,
        backoff_for_streak=lambda streak: float(streak),
    )
    assert capped is not None
    assert capped.delay_s == 30
    assert capped.next_streak == 2

    backed_off = plan_stream_retry(
        retryable=True,
        context_overflow=False,
        streak=1,
        retry_after_s=None,
        max_delay_s=30,
        backoff_for_streak=lambda streak: float(streak * 3),
    )
    assert backed_off is not None
    assert backed_off.delay_s == 6
    assert backed_off.next_streak == 2
