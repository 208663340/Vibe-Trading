"""Parent-evidence gate for description-driven adaptation."""

from __future__ import annotations

from datetime import date
from typing import Sequence

from src.research.strategy_discovery.models import (
    DECAY_STALE,
    QUALITY_ADEQUATE,
    EvidenceRow,
    classify_decay,
)


def parent_adaptation_blockers(
    rows: Sequence[EvidenceRow], today: date
) -> tuple[str, ...]:
    """Return stable refusal reasons, or empty when adaptation may proceed.

    Allowed when at least one row is adequate and not stale. Reasons use
    ``stale-evidence:`` / ``insufficient-evidence:`` prefixes.
    """
    if not rows:
        return ("insufficient-evidence: parent has no evidence rows",)

    has_usable = False
    saw_stale = False
    saw_weak = False
    for row in rows:
        decay = classify_decay(row.date_ranges, today)
        if row.evidence_quality == QUALITY_ADEQUATE and decay != DECAY_STALE:
            has_usable = True
        if decay == DECAY_STALE:
            saw_stale = True
        if row.evidence_quality != QUALITY_ADEQUATE:
            saw_weak = True

    if has_usable:
        return ()

    reasons: list[str] = []
    if saw_stale:
        reasons.append(
            "stale-evidence: parent has no fresh or aging adequate evidence"
        )
    if saw_weak or not saw_stale:
        reasons.append("insufficient-evidence: parent has no adequate evidence")
    return tuple(reasons)
