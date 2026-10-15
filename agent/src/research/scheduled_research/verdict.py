"""The machine-readable `## Verdict` tail of a scheduled-research briefing.

Playbooks declare the section as their final output block: one line per
tracked symbol, ``- SYMBOL: STATE - one short reason``, where STATE is a
single upper-case token the playbook declares. The heading with no lines
under it is a real answer ("no calls this run"), not a missing one.

Parsing is strict on purpose: the Market Watch list renders this record
without ever re-reading the free text, so anything malformed must degrade to
``contract_violation`` rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: Parse states persisted on the record.
PARSE_OK = "ok"
PARSE_NO_SECTION = "no_verdict_section"
PARSE_CONTRACT_VIOLATION = "contract_violation"

_HEADING_RE = re.compile(r"^##\s+Verdict\s*$", re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^##\s", re.MULTILINE)
#: `- SYMBOL: STATE - reason` (reason optional). STATE stays an opaque token
#: to the parser; each playbook declares its own small vocabulary.
_ITEM_RE = re.compile(
    r"^-\s+([A-Z0-9][A-Z0-9._-]*)\s*:\s*([A-Z][A-Z0-9-]*)(?:\s+-\s+(.+?))?\s*$"
)


@dataclass
class VerdictItem:
    """One tracked symbol's verdict line."""

    symbol: str
    state: str
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"symbol": self.symbol, "state": self.state, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VerdictItem":
        return cls(
            symbol=str(data.get("symbol", "")),
            state=str(data.get("state", "")),
            reason=str(data.get("reason", "")),
        )


@dataclass
class VerdictRecord:
    """A run's parsed verdict, persisted on the job at its terminal state.

    Attributes:
        session_id: The session whose terminal briefing this was parsed from.
        recorded_at: Epoch-millisecond timestamp of the write; the UI leans on
            it to render stale verdicts as stale.
        parse: One of the ``PARSE_*`` states. ``no_verdict_section`` is the
            permanent state of ad-hoc prompts and renders as nothing, never as
            a warning.
        outcome: Server-derived headline: ``no_calls`` when nothing was
            tracked, the single shared state when every item agrees, else
            ``mixed``.
        items: The parsed symbol lines, empty exactly when parse is ``ok``
            and nothing was tracked.
        previous: The prior run's record, embedded at write time so the list
            can render a delta without a second query. One level deep only.
    """

    session_id: str
    recorded_at: int
    parse: str
    outcome: str
    items: List[VerdictItem] = field(default_factory=list)
    previous: Optional["VerdictRecord"] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "recorded_at": self.recorded_at,
            "parse": self.parse,
            "outcome": self.outcome,
            "items": [item.to_dict() for item in self.items],
            "previous": self.previous.to_dict() if self.previous else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VerdictRecord":
        previous = data.get("previous")
        return cls(
            session_id=str(data.get("session_id", "")),
            recorded_at=int(data.get("recorded_at", 0)),
            parse=str(data.get("parse", PARSE_NO_SECTION)),
            outcome=str(data.get("outcome", "no_calls")),
            items=[VerdictItem.from_dict(i) for i in data.get("items", [])],
            previous=cls.from_dict(previous) if isinstance(previous, dict) else None,
        )


def parse_verdict_section(briefing: str) -> tuple[str, List[VerdictItem]]:
    """Parse the ``## Verdict`` tail of a terminal briefing.

    Returns:
        ``(parse_state, items)``. ``no_verdict_section`` when no such heading
        exists, ``contract_violation`` when it exists but a body line does not
        match the contract, and ``ok`` otherwise, including an empty section.
    """
    heading = _HEADING_RE.search(briefing)
    if heading is None:
        return PARSE_NO_SECTION, []

    body = briefing[heading.end() :]
    next_heading = _NEXT_HEADING_RE.search(body)
    if next_heading is not None:
        body = body[: next_heading.start()]

    items: List[VerdictItem] = []
    for line in body.split("\n"):
        line = line.strip()
        if not line:
            continue
        match = _ITEM_RE.match(line)
        if match is None:
            return PARSE_CONTRACT_VIOLATION, []
        symbol, state, reason = match.groups()
        items.append(VerdictItem(symbol=symbol, state=state, reason=reason or ""))
    return PARSE_OK, items


def outcome_of(items: List[VerdictItem]) -> str:
    """The server-derived headline for a parsed item list."""
    if not items:
        return "no_calls"
    states = {item.state for item in items}
    return states.pop() if len(states) == 1 else "mixed"
