"""Parent-evidence gate for description-driven adaptation."""

from __future__ import annotations

from datetime import date, timedelta

from src.research.strategy_discovery.adaptation import parent_adaptation_blockers
from src.research.strategy_discovery.models import (
    DECAY_STALE,
    MIN_TRADES,
    QUALITY_ADEQUATE,
    QUALITY_INSUFFICIENT,
    EvidenceRow,
    classify_decay,
    classify_quality,
    coverage_days_from_ranges,
)

JAN_2026 = ("2026-01 to 2026-01",)
HEALTHY_RANGES = ("2023-01 to 2025-12", "2026-01 to 2026-01")
TODAY_FRESH = date(2026, 1, 31)
TODAY_STALE = TODAY_FRESH + timedelta(days=180)


def _row(
    *,
    trades: int,
    date_ranges: tuple[str, ...],
    evidence_quality: str | None = None,
) -> EvidenceRow:
    coverage = coverage_days_from_ranges(date_ranges)
    quality = evidence_quality if evidence_quality is not None else classify_quality(trades, coverage)
    return EvidenceRow(
        strategy_id="sdm:parent",
        regime="bull_market",
        trades_in_regime=trades,
        date_ranges=date_ranges,
        evidence_quality=quality,
        evidence_stage="backtest",
        provenance="/tmp/run",
    )


class TestParentAdaptationBlockers:
    def test_healthy_adequate_fresh_window_is_not_blocked(self) -> None:
        row = _row(trades=20, date_ranges=HEALTHY_RANGES)
        assert row.evidence_quality == QUALITY_ADEQUATE
        assert classify_decay(row.date_ranges, TODAY_FRESH) != DECAY_STALE
        assert classify_decay(JAN_2026, TODAY_FRESH) != DECAY_STALE
        assert parent_adaptation_blockers((row,), TODAY_FRESH) == ()

    def test_stale_window_blocks_with_stale_evidence(self) -> None:
        row = _row(trades=20, date_ranges=HEALTHY_RANGES)
        assert row.evidence_quality == QUALITY_ADEQUATE
        assert classify_decay(row.date_ranges, TODAY_STALE) == DECAY_STALE
        blockers = parent_adaptation_blockers((row,), TODAY_STALE)
        assert blockers
        assert any(reason.startswith("stale-evidence:") for reason in blockers)

    def test_insufficient_trades_blocks_with_insufficient_evidence(self) -> None:
        row = _row(trades=4, date_ranges=HEALTHY_RANGES)
        assert 4 < MIN_TRADES
        assert row.evidence_quality == QUALITY_INSUFFICIENT
        assert classify_decay(row.date_ranges, TODAY_FRESH) != DECAY_STALE
        blockers = parent_adaptation_blockers((row,), TODAY_FRESH)
        assert blockers
        assert any(reason.startswith("insufficient-evidence:") for reason in blockers)
        assert not any(reason.startswith("stale-evidence:") for reason in blockers)

    def test_empty_rows_block(self) -> None:
        blockers = parent_adaptation_blockers((), TODAY_FRESH)
        assert blockers
        assert blockers == ("insufficient-evidence: parent has no evidence rows",)
