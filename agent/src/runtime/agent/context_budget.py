"""Size context compaction from the model's real window and real token usage.

Compaction used to trigger at a fixed 40K *estimated* tokens (``chars / 4`` of
the messages, tool schemas excluded), with layer 1 clearing every tool result
but the last three once the estimate passed 20K. The system prompt alone
estimates at ~12.5K, so a cross-sectional question (a dozen companies' filings)
lost its evidence from the third iteration on, re-fetched it, got byte-identical
results and ended in ``no_progress``. gpt-6-sol has a 272K window and
deepseek-v4-pro 1M; the old budget used a few percent of either.

The budget here is the model's window (env override > limit learned from a
context-length error > ``context_windows.json`` > default), capped for cost,
minus the prompt's static part (system prompt + tool schemas). Layers 1-3 fire
at fixed fractions of what remains for the conversation.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "providers" / "context_windows.json"

#: Share of the window a prompt may use; the rest is left for the reply,
#: reasoning tokens and the error of estimating growth between calls.
USABLE_FRACTION = 0.8
#: Layer 1 (clear old tool results, oldest first, only back down to this line)
#: and layer 2 (fold long text) start at these shares of the conversation
#: budget; layer 3 (summary) at all of it. Calibrated so the largest healthy
#: run measured on 2026-09-29 (154K real input, ~48K of it static) loses
#: nothing: with the 200K cost cap, layer 1 starts at ~170K.
MICRO_FRACTION = 0.8
COLLAPSE_FRACTION = 0.9
#: Minimum regrowth after compaction before paying for another summary.
MIN_CONVERSATION_TOKENS = 8_000

_LIMIT_RE = re.compile(
    r"(?:maximum context length|context length|context window|token limit|maximum of)"
    r"\D{0,40}?(\d[\d,]{3,})",
    re.IGNORECASE,
)
_warned_token_threshold = False


@lru_cache(maxsize=1)
def _catalog() -> dict[str, Any]:
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def estimate_tokens(value: Any) -> int:
    """Rough token count (~4 chars/token) of any JSON-serialisable value."""
    return len(json.dumps(value, default=str, ensure_ascii=False)) // 4


def resolve_window(
    provider: str,
    model: str,
    *,
    env_window: Optional[int] = None,
    learned_window: Optional[int] = None,
) -> tuple[int, str]:
    """Return the model's context window and where the number came from.

    Args:
        provider: Canonical provider name (``openai-codex``, ``deepseek`` ...).
        model: Model name as configured.
        env_window: ``VIBE_TRADING_CONTEXT_WINDOW``, when set.
        learned_window: Limit read from a context-length error in this run.

    Returns:
        ``(window_tokens, source)``.
    """
    _warn_deprecated_token_threshold()
    if env_window:
        return int(env_window), "env:VIBE_TRADING_CONTEXT_WINDOW"
    if learned_window:
        return int(learned_window), "learned:context_length_error"
    catalog = _catalog()
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    for entry in catalog["models"]:
        if entry.get("provider") and entry["provider"] != provider:
            continue
        if re.search(entry["pattern"], model, re.IGNORECASE):
            return int(entry["window"]), f"catalog:{entry['pattern']}"
    return int(catalog["default_window"]), "default"


def _warn_deprecated_token_threshold() -> None:
    global _warned_token_threshold
    if _warned_token_threshold or "TOKEN_THRESHOLD" not in os.environ:
        return
    _warned_token_threshold = True
    logger.warning(
        "TOKEN_THRESHOLD is deprecated and ignored: it measured chars/4 of the "
        "messages without tool schemas. Compaction now follows the model's real "
        "context window; set VIBE_TRADING_CONTEXT_WINDOW to override it."
    )


def is_context_overflow(error_text: str) -> bool:
    """Whether a provider error says the prompt exceeded the context window."""
    lowered = (error_text or "").lower()
    return any(pattern in lowered for pattern in _catalog()["overflow_error_patterns"])


def parse_context_limit(error_text: str) -> Optional[int]:
    """Read the context limit a provider states in its overflow error, if any."""
    match = _LIMIT_RE.search(error_text or "")
    if not match:
        return None
    value = int(match.group(1).replace(",", ""))
    return value if value >= 1_000 else None


@dataclass(frozen=True)
class CompactionBudget:
    """Real-token thresholds for the three compaction layers.

    Attributes:
        window: Model context window in tokens.
        source: Where ``window`` came from.
        max_tokens: Cost ceiling on the prompt size, whatever the window.
        static_tokens: Tokens of the part compaction cannot shrink (system
            prompt + tool schemas).
    """

    window: int
    source: str
    max_tokens: int
    static_tokens: int

    @property
    def total(self) -> int:
        """Largest prompt, in real tokens, the loop lets a run grow to."""
        return min(int(self.window * USABLE_FRACTION), self.max_tokens)

    @property
    def conversation(self) -> int:
        """Tokens left for the conversation after the static prompt."""
        return max(0, self.total - self.static_tokens)

    @property
    def micro_at(self) -> int:
        return self.static_tokens + int(self.conversation * MICRO_FRACTION)

    @property
    def collapse_at(self) -> int:
        return self.static_tokens + int(self.conversation * COLLAPSE_FRACTION)

    @property
    def compact_at(self) -> int:
        return self.static_tokens + self.conversation


@dataclass(frozen=True)
class RequestSnapshot:
    """Immutable estimates of the main request before dispatch."""

    request_id: int
    model: str | None
    messages: int
    tools: int | None
    conversation: int


class ContextMeter:
    """Calibrate only main requests, including their actual tools/model shape."""

    def __init__(self, estimator: Callable[[Any], int] = estimate_tokens) -> None:
        self._estimate = estimator
        self._anchor: tuple[int, RequestSnapshot] | None = None
        self.static_tokens: int | None = None
        self._request_id = 0

    def snapshot(self, messages: list, tool_schema_tokens: int | None = None,
                 *, model: str | None = None) -> RequestSnapshot:
        self._request_id += 1
        return RequestSnapshot(self._request_id, model, self._estimate(messages),
                               tool_schema_tokens, self._estimate(messages[1:]))

    def observe(self, input_tokens: Optional[int], messages: list | None = None,
                *, request: RequestSnapshot | None = None) -> None:
        """Valid usage anchors its captured request; legacy message calls work."""
        if not isinstance(input_tokens, int) or isinstance(input_tokens, bool) or input_tokens <= 0:
            return
        if request is None:
            request = self.snapshot(messages if messages is not None else [])
        if self._anchor is not None and request.request_id < self._anchor[1].request_id:
            return
        self._anchor = (input_tokens, request)
        self.static_tokens = max(0, input_tokens - request.conversation)

    def _matching_anchor(self, model: str | None):
        if self._anchor is not None and model is not None and self._anchor[1].model != model:
            self._anchor = None
            self.static_tokens = None
        return self._anchor

    def tokens(self, messages: list, tool_schema_tokens: int, *, model: str | None = None) -> int:
        anchor = self._matching_anchor(model)
        if anchor is None:
            return self._estimate(messages) + tool_schema_tokens
        actual, request = anchor
        tools_delta = 0 if request.tools is None else tool_schema_tokens - request.tools
        return max(0, actual + self._estimate(messages) - request.messages + tools_delta)

    def static(self, messages: list, tool_schema_tokens: int, *, model: str | None = None) -> int:
        anchor = self._matching_anchor(model)
        if anchor is not None and self.static_tokens is not None:
            tools_delta = 0 if anchor[1].tools is None else tool_schema_tokens - anchor[1].tools
            return max(0, self.static_tokens + tools_delta)
        return self._estimate(messages[:1]) + tool_schema_tokens
