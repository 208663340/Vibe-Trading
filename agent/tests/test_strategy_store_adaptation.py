"""Adaptation writes a new CREATED strategy through the artifact store."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.research.strategy_store.adaptation import (
    AdaptationError,
    AdaptationPatch,
    adapt_artifact,
    register_adaptation,
)
from src.research.strategy_store.models import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
    ValidationStatus,
)
from src.research.strategy_discovery.models import (
    EvidenceRow,
    classify_quality,
    coverage_days_from_ranges,
)
from src.research.strategy_store.store import InMemoryStrategyStore


@pytest.fixture(autouse=True)
def _reset_store(tmp_path):
    import src.research.strategy_store._shared as shared
    from src.research.strategy_store.sqlite_store import SqliteStrategyStore

    shared._store = SqliteStrategyStore(db_path=tmp_path / "test.db")
    yield
    shared._store = None


HEALTHY_RANGES = ("2023-01 to 2025-12", "2026-01 to 2026-01")
TODAY_FRESH = date(2026, 1, 31)
TODAY_STALE = TODAY_FRESH + timedelta(days=180)


def _evidence(
    *, trades: int = 20, date_ranges: tuple[str, ...] = HEALTHY_RANGES
) -> tuple[EvidenceRow, ...]:
    """One evidence row for the parent, adequate and fresh unless told otherwise."""
    coverage = coverage_days_from_ranges(date_ranges)
    return (
        EvidenceRow(
            strategy_id="sdm:parent",
            regime="bull_market",
            trades_in_regime=trades,
            date_ranges=date_ranges,
            evidence_quality=classify_quality(trades, coverage),
            evidence_stage="backtest",
            provenance="/tmp/run",
        ),
    )


SIGNAL = "close > sma(20)"
ENTRY = "buy next open"
EXIT = "sell next open"
ENGINE = "engines/momentum.py"


def _strategy(
    *,
    artifact_id: str = "art_parent",
    name: str = "momentum",
    universe: str = "CSI300",
    artifact_type: ArtifactType = ArtifactType.STRATEGY,
    **overrides,
) -> Artifact:
    fields = dict(
        id=artifact_id,
        type=artifact_type,
        name=name,
        universe=universe,
        signal_definition=SIGNAL,
        entry_rules=ENTRY,
        exit_rules=EXIT,
        position_sizing="monthly rebalance",
        signal_engine_path=ENGINE,
        run_dir="/tmp/parent_run",
        status=ArtifactStatus.ACTIVE,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
        developer="Ada",
        owner="Bob",
        theme=("momentum",),
        columns_required=("close",),
        decay_horizon=20,
        source_paper="Jegadeesh 1993",
        hypothesis_id="hyp_1",
        validation_status=ValidationStatus.UNVALIDATED,
    )
    fields.update(overrides)
    return Artifact(**fields)


class TestAdaptArtifact:
    def test_child_keeps_parent_signal_fields(self) -> None:
        parent = _strategy()
        child = adapt_artifact(parent, AdaptationPatch(universe="SP500"))
        assert child.signal_definition == SIGNAL
        assert child.entry_rules == ENTRY
        assert child.exit_rules == EXIT
        assert child.signal_engine_path == ENGINE
        assert child.theme == parent.theme
        assert child.columns_required == parent.columns_required
        assert child.decay_horizon == parent.decay_horizon
        assert child.source_paper == parent.source_paper
        assert child.hypothesis_id == parent.hypothesis_id
        assert child.developer == parent.developer
        assert child.owner == parent.owner

    def test_child_identity_starts_clean(self) -> None:
        parent = _strategy()
        child = adapt_artifact(parent, AdaptationPatch(universe="SP500"))
        assert child.derived_from == parent.id
        assert child.id == ""
        assert child.run_dir is None
        assert child.status is ArtifactStatus.CREATED
        assert child.created_at == ""
        assert child.updated_at == ""
        assert child.disabled_at is None
        assert child.disabled_reason is None
        assert child.validation_status is ValidationStatus.UNVALIDATED
        assert child.validation_date is None

    def test_factor_parent_refused(self) -> None:
        parent = _strategy(artifact_type=ArtifactType.FACTOR)
        with pytest.raises(AdaptationError, match="strategy"):
            adapt_artifact(parent, AdaptationPatch(universe="SP500"))

    def test_noop_same_name_and_universe_refused(self) -> None:
        parent = _strategy()
        with pytest.raises(AdaptationError, match="adaptation must change universe or name"):
            adapt_artifact(parent, AdaptationPatch(position_sizing="weekly rebalance"))


class TestRegisterAdaptation:
    def test_new_universe_same_name_succeeds(self) -> None:
        store = InMemoryStrategyStore()
        parent_id = store.register_artifact(_strategy(artifact_id=""))
        child_id = register_adaptation(
            store,
            parent_id,
            AdaptationPatch(universe="SP500"),
            parent_evidence=_evidence(),
            today=TODAY_FRESH,
        )
        child = store.get_artifact(child_id)
        parent = store.get_artifact(parent_id)
        assert child is not None
        assert parent is not None
        assert child.id == child_id
        assert child.id != parent_id
        assert child.derived_from == parent_id
        assert child.run_dir is None
        assert child.status is ArtifactStatus.CREATED
        assert child.name == parent.name
        assert child.universe == "SP500"

    def test_same_universe_same_name_raises_duplicate(self) -> None:
        store = InMemoryStrategyStore()
        parent_id = store.register_artifact(_strategy(artifact_id=""))
        register_adaptation(
            store,
            parent_id,
            AdaptationPatch(name="momentum_weekly"),
            parent_evidence=_evidence(),
            today=TODAY_FRESH,
        )
        with pytest.raises(ValueError, match="already exists"):
            register_adaptation(
                store,
                parent_id,
                AdaptationPatch(name="momentum_weekly"),
                parent_evidence=_evidence(),
                today=TODAY_FRESH,
            )


class TestSqliteDerivedFromRoundtrip:
    def test_derived_from_survives_sqlite_roundtrip(self) -> None:
        from src.research.strategy_store._shared import get_store

        store = get_store()
        parent_id = store.register_artifact(_strategy(artifact_id=""))
        child_id = register_adaptation(
            store,
            parent_id,
            AdaptationPatch(universe="SP500"),
            parent_evidence=_evidence(),
            today=TODAY_FRESH,
        )
        fetched = store.get_artifact(child_id)
        assert fetched is not None
        assert fetched.derived_from == parent_id
        assert fetched.id == child_id
        assert fetched.id != parent_id
        assert fetched.run_dir is None
        assert fetched.status is ArtifactStatus.CREATED
        assert fetched.signal_definition == SIGNAL


class TestChildDoesNotInheritAttestation:
    """The child may not wear an attestation it never earned.

    ``adapt_artifact`` copies through ``dataclasses.replace``, so every
    governance field the call does not name survives onto the child. The
    parent's ``validator`` / ``approver`` name people who signed off on a
    *different* artifact, and the child is UNVALIDATED with no run behind
    it — the same inherited-provenance rule that keeps the parent's evidence
    off the child. Asserted per field, and cross-checked against the fields
    that are meant to carry forward, so a future blanket reset is caught too.
    """

    def _signed_parent(self) -> Artifact:
        return _strategy(
            validator="Val",
            approver="Cho",
            artifact_version="3",
            model_version="gpt-x-2025",
            validation_status=ValidationStatus.VALIDATED,
            validation_date="2026-01-05T00:00:00+00:00",
            model_tier=None,
            intended_use="CSI300 momentum sleeve",
            limitations="thin coverage before 2015",
        )

    def test_attestation_fields_are_cleared(self) -> None:
        parent = self._signed_parent()
        child = adapt_artifact(parent, AdaptationPatch(universe="SP500"))

        assert child.validation_status is ValidationStatus.UNVALIDATED
        assert child.validation_date is None
        assert child.validator is None, "child claims an independent validator it never had"
        assert child.approver is None, "child claims a sign-off it never received"
        assert child.artifact_version is None, "child is version 1 of its own lineage"
        assert child.model_version is None, "no model has generated the child's code yet"

    def test_authorship_and_declared_use_still_carry_forward(self) -> None:
        """The reset must be surgical: ownership and scope are not attestations."""
        parent = self._signed_parent()
        child = adapt_artifact(parent, AdaptationPatch(universe="SP500"))

        assert child.developer == parent.developer
        assert child.owner == parent.owner
        assert child.intended_use == parent.intended_use
        assert child.limitations == parent.limitations

    def test_cleared_child_is_not_a_four_eyes_violation(self) -> None:
        """A same-person dev/approve pair must not survive the copy either."""
        from src.research.strategy_store.models import is_four_eyes_violation

        parent = _strategy(developer="Ada", approver="Ada")
        assert is_four_eyes_violation(parent)
        assert not is_four_eyes_violation(adapt_artifact(parent, AdaptationPatch(universe="SP500")))


class TestEvidenceGateSitsOnTheWritePath:
    """``parent_adaptation_blockers`` has to be reachable from the write, not
    just from a caller who remembers to ask.

    The gate inputs are keyword-only and required, so a caller cannot skip
    the check by forgetting it — the call does not typecheck or run without
    them. These tests pin the refusal itself rather than the signature, so
    the guarantee survives a refactor of how the inputs arrive.
    """

    def test_stale_parent_evidence_refuses_the_write(self) -> None:
        store = InMemoryStrategyStore()
        parent_id = store.register_artifact(_strategy(artifact_id=""))
        before = len(store.list_artifacts())

        with pytest.raises(AdaptationError, match="stale-evidence"):
            register_adaptation(
                store,
                parent_id,
                AdaptationPatch(universe="SP500"),
                parent_evidence=_evidence(),
                today=TODAY_STALE,
            )

        assert len(store.list_artifacts()) == before, "refused adaptation still wrote a child"

    def test_insufficient_parent_evidence_refuses_the_write(self) -> None:
        store = InMemoryStrategyStore()
        parent_id = store.register_artifact(_strategy(artifact_id=""))

        with pytest.raises(AdaptationError, match="insufficient-evidence"):
            register_adaptation(
                store,
                parent_id,
                AdaptationPatch(universe="SP500"),
                parent_evidence=(),
                today=TODAY_FRESH,
            )

    def test_gate_inputs_are_required(self) -> None:
        """Omitting them is a TypeError, not a silent unguarded write."""
        store = InMemoryStrategyStore()
        parent_id = store.register_artifact(_strategy(artifact_id=""))

        with pytest.raises(TypeError):
            register_adaptation(store, parent_id, AdaptationPatch(universe="SP500"))

    def test_missing_parent_is_reported_before_the_gate(self) -> None:
        store = InMemoryStrategyStore()
        with pytest.raises(AdaptationError, match="not found"):
            register_adaptation(
                store,
                "art_missing",
                AdaptationPatch(universe="SP500"),
                parent_evidence=(),
                today=TODAY_FRESH,
            )
