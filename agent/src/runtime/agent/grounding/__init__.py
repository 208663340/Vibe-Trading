"""Run-scoped identity and numeric evidence gates for the main agent loop.

The language model remains responsible for research and explanation, but three
facts are structural rather than advisory:

* a market-data consumer may only use an identity that was locked before the
  current assistant tool-call batch started;
* a final price claim may not contradict the full, untruncated tool result; and
* a figure may not be attached to an instrument that no tool call in this run
  ever passed in or returned.

Those are the mechanically decidable parts of the agent's output principles.
The rest of that contract — "state the as-of", "analysis, not advice", "refuse
out loud" — stays in the system prompt on purpose.

What a number IS — an observed print, a derived level, a proposal, a citation,
a count — is declared by the model in a ``figures`` block and verified against
evidence (:mod:`figures`, :mod:`policies`). It used to be inferred from the
prose around the number, against a catalogue of price words, level words,
indicator names, attribution verbs and forecast frames. A catalogue is only as
complete as the day it was typed: the same sentence got opposite verdicts in
its two translations, and each missing phrasing was either a rejected correct
answer or a released fabrication.

The package deliberately contains no provider or tool-registry dependencies so
its state machine and final-answer checks remain deterministic and testable.
The split follows the data flow: :mod:`identity` locks what a number is about,
:mod:`evidence` records what the run actually observed, :mod:`figures` finds
the numbers in a draft, :mod:`policies` decides whether each one is grounded,
:mod:`release` turns a rejection into a correction or a discounted release, and
:mod:`ledger` is the facade the agent loop drives.
"""

from __future__ import annotations

from src.runtime.agent.resolution_context import IdentityConstraint, ResolutionContext

from src.runtime.agent.grounding.identity import IdentityRecord, ToolAuthorization
from src.runtime.agent.grounding.evidence import EvidenceRecord
from src.runtime.agent.grounding.policies import ValidationResult
from src.runtime.agent.grounding.ledger import GROUNDING_ARTIFACT, GroundingLedger

__all__ = [
    "GROUNDING_ARTIFACT",
    "EvidenceRecord",
    "GroundingLedger",
    "IdentityConstraint",
    "IdentityRecord",
    "ResolutionContext",
    "ToolAuthorization",
    "ValidationResult",
]
