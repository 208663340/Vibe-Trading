"""Pure decisions for one agent turn.

Callers pass snapshots and receive a decision. Nothing here reads the
filesystem, starts a thread, publishes an event, or touches an AgentLoop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from src.runtime.agent.context_budget import MIN_CONVERSATION_TOKENS


class CompactionView(Protocol):
    """Thresholds the loop already computed for the active model."""

    static_tokens: int
    total: int
    micro_at: int
    collapse_at: int
    compact_at: int


def static_context_exceeds_budget(budget: CompactionView) -> bool:
    """The unshrinkable prompt is already larger than the usable window."""
    return budget.static_tokens >= budget.total


def static_context_error(budget: CompactionView) -> str:
    """Actionable failure when compaction cannot create room."""
    return (
        f"static context ({budget.static_tokens} tokens) exceeds usable context "
        f"budget ({budget.total}); reduce the system prompt/tool schemas or "
        "configure a model with a larger context window"
    )


def needs_microcompact(tokens: int, budget: CompactionView) -> bool:
    return tokens > budget.micro_at


def needs_collapse(tokens: int, budget: CompactionView) -> bool:
    return tokens > budget.collapse_at


def needs_summary(
    tokens: int,
    budget: CompactionView,
    *,
    force: bool,
    compacted_to_tokens: int | None,
    min_regrowth: int = MIN_CONVERSATION_TOKENS,
) -> bool:
    """Summarise on overflow, or after the conversation regrows past the last summary."""
    regrown = (
        compacted_to_tokens is None
        or tokens > compacted_to_tokens + min_regrowth
    )
    return bool(force or (tokens > budget.compact_at and regrown))


def needs_legacy_micro(tokens: int, threshold: int) -> bool:
    return tokens > int(threshold * 0.5)


def needs_legacy_collapse(tokens: int, threshold: int) -> bool:
    return tokens > int(threshold * 0.7)


def needs_legacy_summary(tokens: int, threshold: int) -> bool:
    return tokens > threshold


def learned_context_window(stated: int | None, prompt_tokens: int) -> int:
    """Prefer the provider's stated limit; otherwise shrink the prompt that did not fit."""
    return stated or int(prompt_tokens * 0.9)


def suppress_tools(
    iteration: int,
    max_iterations: int,
    grounding_correction_text_only: bool,
) -> bool:
    """The last iteration, and an ordinary grounding rewrite, must not start new tool work."""
    return iteration == max_iterations or grounding_correction_text_only


def wrap_up_due(iteration: int, wrap_up_at: int, max_iterations: int) -> bool:
    return iteration == wrap_up_at and 1 < iteration < max_iterations


def pending_write_due(iteration: int, max_iterations: int) -> bool:
    """The iteration before the text-only finale is the last chance to write."""
    return iteration == max_iterations - 1


def wrap_up_message(remaining: int, max_iterations: int, pending_directive: str = "") -> str:
    content = (
        f"[SYSTEM] You have {remaining} iterations remaining out of "
        f"{max_iterations}. Wrap up your work now: finish any "
        "outstanding file writes or data updates that are part of your "
        "task FIRST, while tools are still available for a few more "
        "iterations. Do not start new analysis or re-verify data you "
        "already hold. Then provide your final answer as plain text; "
        "if your task was to update a file, state that it is done and where."
    )
    if pending_directive:
        content += "\n\n" + pending_directive
    return content


@dataclass(frozen=True)
class StreamRetryPlan:
    """One retry of a failed provider stream. ``reason`` is the existing SSE reason."""

    reason: str
    delay_s: float
    next_streak: int


def plan_stream_retry(
    *,
    retryable: bool,
    context_overflow: bool,
    streak: int,
    retry_after_s: float | None,
    max_delay_s: float,
    backoff_for_streak: Callable[[int], float],
) -> StreamRetryPlan | None:
    """Return a retry plan, or None when the caller must raise the provider error.

    Context overflow does not consume the transient-failure streak. A
    Retry-After value is capped by ``max_delay_s``; otherwise the delay is the
    backoff for the streak after this failure.
    """
    if not retryable and not context_overflow:
        return None
    if context_overflow:
        return StreamRetryPlan("context_overflow", 0.0, streak)
    next_streak = streak + 1
    if retry_after_s is not None:
        delay_s = min(retry_after_s, max_delay_s)
    else:
        delay_s = backoff_for_streak(next_streak)
    return StreamRetryPlan("provider_stream_retry", delay_s, next_streak)
