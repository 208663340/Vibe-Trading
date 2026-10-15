"""Description-driven adaptation of SDM strategy artifacts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import TYPE_CHECKING, Protocol, Sequence

from src.research.strategy_store.models import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    ValidationStatus,
)

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, see register_adaptation
    from src.research.strategy_discovery.models import EvidenceRow


class AdaptationError(ValueError):
    """Raised when a parent cannot be adapted under the Phase 3 contract."""


@dataclass(frozen=True)
class AdaptationPatch:
    """Allowed description-driven changes. Signal fields are not on this type."""

    universe: str | None = None
    name: str | None = None
    position_sizing: str | None = None


class _ArtifactStore(Protocol):
    def get_artifact(self, artifact_id: str) -> Artifact | None: ...

    def register_artifact(self, artifact: Artifact) -> str: ...


def adapt_artifact(parent: Artifact, patch: AdaptationPatch) -> Artifact:
    """Return a new strategy artifact derived from *parent*. Does not write."""
    if parent.type is not ArtifactType.STRATEGY:
        raise AdaptationError(
            f"only strategy artifacts can be adapted; got type={parent.type.value!r}"
        )
    if not parent.id:
        raise AdaptationError("parent artifact id is required")

    new_universe = patch.universe if patch.universe is not None else parent.universe
    new_name = patch.name if patch.name is not None else parent.name
    if new_universe == parent.universe and new_name == parent.name:
        raise AdaptationError("adaptation must change universe or name")

    new_sizing = (
        patch.position_sizing if patch.position_sizing is not None else parent.position_sizing
    )
    # ``replace`` copies every field not named here, so the governance block
    # (models.py: "who built it, who owns it, who independently validated it,
    # who signed off, at what version") has to be split deliberately.
    #
    # ``developer`` / ``owner`` carry forward: the same team owns the derived
    # strategy. ``model_tier`` / ``intended_use`` / ``limitations`` describe
    # what the signal is for, which adaptation does not change.
    #
    # ``validator`` / ``approver`` do NOT. They name people who attested to
    # *this* artifact, and the child is UNVALIDATED with no run behind it —
    # carrying their names is the parent's attestation worn by something that
    # never earned it, the same inherited-provenance rule that keeps evidence
    # off the child. ``artifact_version`` / ``model_version`` go with them:
    # the child is version 1 of its own lineage, and no model has generated
    # its code yet (``run_dir`` is None until its own backtest runs).
    return replace(
        parent,
        id="",
        name=new_name,
        universe=new_universe,
        position_sizing=new_sizing,
        derived_from=parent.id,
        status=ArtifactStatus.CREATED,
        run_dir=None,
        created_at="",
        updated_at="",
        disabled_at=None,
        disabled_reason=None,
        validator=None,
        approver=None,
        artifact_version=None,
        model_version=None,
        validation_status=ValidationStatus.UNVALIDATED,
        validation_date=None,
    )


def register_adaptation(
    store: _ArtifactStore,
    parent_id: str,
    patch: AdaptationPatch,
    *,
    parent_evidence: "Sequence[EvidenceRow]",
    today: date,
) -> str:
    """Persist an adapted child through the strategy store. Returns the new id.

    ``parent_evidence`` and ``today`` are keyword-only and required so the
    Phase 3 evidence gate cannot be skipped by forgetting to call it: the
    function will not run without the inputs the gate needs. Pass the rows
    the facade's ``get_strategy_evidence`` returned for *parent_id*; an empty
    sequence is a refusal, not a bypass.

    Args:
        store: Artifact store to read the parent from and write the child to.
        parent_id: Identifier of the strategy being adapted.
        patch: Description-driven changes; signal fields are not on this type.
        parent_evidence: Evidence rows computed for *parent_id*.
        today: Reference date the decay verdict is taken against.

    Returns:
        The new child artifact's id.

    Raises:
        AdaptationError: if the parent is missing, is not adaptable, or has
            no adequate, non-stale evidence row.
    """
    from src.research.strategy_discovery.adaptation import parent_adaptation_blockers

    parent = store.get_artifact(parent_id)
    if parent is None:
        raise AdaptationError(f"parent artifact {parent_id!r} not found")

    blockers = parent_adaptation_blockers(parent_evidence, today)
    if blockers:
        raise AdaptationError(
            f"parent artifact {parent_id!r} cannot be adapted: " + "; ".join(blockers)
        )

    child = adapt_artifact(parent, patch)
    return store.register_artifact(child)
