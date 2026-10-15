"""Dispatch-time observation validity across executed mutation barriers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ReplayDecision:
    outcome: str
    raw_result: str = ""
    key: tuple[str, str] | None = None
    recovery_count: int = 0


def readonly_replay_allowed(tool_def: Any, arguments: Mapping[str, Any]) -> bool:
    if tool_def is None or not getattr(tool_def, "is_readonly", False):
        return False
    if getattr(tool_def, "deterministic", False):
        return False
    try:
        if bool((arguments or {}).get("no_cache")):
            return False
    except AttributeError:
        return False
    return not getattr(tool_def, "repeatable", False) or bool(getattr(tool_def, "replay_after_compaction", False))


class ToolReplayPolicy:
    """Use the facade's run-scoped ledgers without changing cache identities."""

    def __init__(self, owner: Any, *, recovery_limit: int):
        self.owner = owner
        self.recovery_limit = recovery_limit

    def decide(self, tool_def: Any, name: str, arguments: Mapping[str, Any]) -> ReplayDecision:
        o = self.owner
        key = o._identical_call_key(name, arguments)
        if key is not None and o._tool_progress.is_blocked(key):
            return ReplayDecision("failed_repeat", key=key)
        if (
            key is not None
            and key in o._readonly_replay_ready
            and o._readonly_replay_recoveries < self.recovery_limit
            and readonly_replay_allowed(tool_def, arguments)
            and key in o._readonly_replay_cache
        ):
            raw = o._readonly_replay_cache[key]
            # Reserve the restoration at dispatch, so a second identical call
            # cannot restore again while the first awaits its ordered commit.
            o._readonly_replay_ready.discard(key)
            o._readonly_replay_recoveries += 1
            o._called_ok.add(key)
            if getattr(tool_def, "repeatable", False):
                o._readonly_replay_protected.add(key)
            return ReplayDecision("replayed", raw, key, o._readonly_replay_recoveries)
        if (
            key is not None
            and key in o._called_ok
            and (not getattr(tool_def, "repeatable", False) or key in o._readonly_replay_protected)
        ):
            return ReplayDecision("skipped", key=key)
        if tool_def is not None and getattr(tool_def, "deterministic", False):
            if key is not None and key in o._called_identical:
                return ReplayDecision("cached", o._called_identical[key], key)
        return ReplayDecision("execute", key=key)

    def invalidate_mutable_observations(self) -> None:
        o = self.owner
        o._readonly_replay_cache.clear()
        o._readonly_replay_ready.clear()
        o._readonly_replay_protected.clear()
