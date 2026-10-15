"""Deterministic artifact integration for Binance USD-M shadow reconciliation."""

from __future__ import annotations

import ast
from copy import deepcopy
import inspect
import json

import pandas as pd
import pytest

import src.backtest.binance_shadow_evidence as evidence_module
from src.backtest.binance_account_reconciliation import (
    BinanceAccountSnapshot,
    BinancePositionSnapshot,
    ReconciliationTolerance,
)
from src.backtest.binance_shadow_evidence import (
    build_binance_drift_evidence,
    build_binance_drift_summary,
    write_binance_drift_evidence,
)
from src.backtest.perpetual_risk import (
    AccountState,
    PositionRisk,
    PositionState,
    RiskSnapshot,
)


OBSERVED_AT = pd.Timestamp("2026-08-27T08:00:00Z")


def _local_cross() -> tuple[AccountState, RiskSnapshot]:
    position = PositionState("BTC-USDT-PERP", 0.1, 60_000.0, 10.0, 3.0, None)
    account = AccountState(1_000.0, (position,), "cross")
    risk = PositionRisk("BTC-USDT-PERP", 61_000.0, 6_100.0, 100.0, 610.0, 24.4, None)
    return account, RiskSnapshot(
        1_100.0,
        610.0,
        24.4,
        490.0,
        (risk,),
        "healthy",
        (),
        ("conservative_intrabar_assumption",),
    )


def _local_isolated() -> tuple[AccountState, RiskSnapshot, BinanceAccountSnapshot]:
    position = PositionState("BTC-USDT-PERP", 0.1, 60_000.0, 10.0, 3.0, 700.0)
    account = AccountState(1_000.0, (position,), "isolated")
    risk = PositionRisk("BTC-USDT-PERP", 61_000.0, 6_100.0, 100.0, 610.0, 24.4, 800.0)
    snapshot = RiskSnapshot(
        1_100.0,
        610.0,
        24.4,
        490.0,
        (risk,),
        "healthy",
        (),
        ("conservative_intrabar_assumption",),
    )
    exchange = _exchange_cross(
        positions=(
            BinancePositionSnapshot(
                "BTC-USDT-PERP",
                0.1,
                60_000.0,
                10.0,
                "isolated",
                700.0,
                100.0,
                610.0,
                24.4,
            ),
        )
    )
    return account, snapshot, exchange


def _exchange_cross(**changes: object) -> BinanceAccountSnapshot:
    values: dict[str, object] = {
        "schema_version": "binance-usdm-account-observation-v1",
        "observed_at": OBSERVED_AT,
        "source": "binance-usdm",
        "source_profile": "binance-live-sdk-readonly",
        "configuration_hash": "a" * 64,
        "data_status": "complete",
        "wallet_balance": 1_000.0,
        "margin_balance": 1_100.0,
        "available_balance": 490.0,
        "total_unrealized_pnl": 100.0,
        "total_initial_margin": 610.0,
        "total_maintenance_margin": 24.4,
        "positions": (
            BinancePositionSnapshot(
                "BTC-USDT-PERP",
                0.1,
                60_000.0,
                10.0,
                "cross",
                None,
                100.0,
                610.0,
                24.4,
            ),
        ),
        "fidelity_flags": (
            "client_observation_time",
            "sequential_signed_reads",
        ),
    }
    values.update(changes)
    return BinanceAccountSnapshot(**values)  # type: ignore[arg-type]


def _tolerance() -> ReconciliationTolerance:
    return ReconciliationTolerance(
        absolute=0.01,
        relative=0.001,
        max_timestamp_skew_seconds=2.0,
        version="shadow-live-tolerance-v1",
    )


def test_complete_evidence_is_deterministic_and_never_claims_engine_validation() -> None:
    account, risk = _local_cross()

    first = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )
    second = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    assert first == second
    assert first["schema_version"] == "binance-usdm-drift-evidence-v1"
    assert first["status"] == "comparison_complete"
    assert first["severity"] == "none"
    assert first["has_drift"] is False
    assert first["expected_at"] == "2026-08-27T08:00:00+00:00"
    assert first["observed_at"] == "2026-08-27T08:00:00+00:00"
    assert first["tolerance"] == {
        "absolute": 0.01,
        "relative": 0.001,
        "max_timestamp_skew_seconds": 2.0,
        "version": "shadow-live-tolerance-v1",
    }
    assert first["comparison_scope"] == "account_snapshot_fields_only"
    assert first["liquidation_engine_assessment"] == "not_assessed"
    assert first["rejection"] is None
    assert len(first["comparisons"]) == 12
    assert all(item["within_tolerance"] for item in first["comparisons"])
    assert first["fidelity_flags"] == [
        "conservative_intrabar_assumption",
        "client_observation_time",
        "sequential_signed_reads",
        "account_snapshot_comparison_only",
    ]


def test_drift_evidence_preserves_numeric_structural_and_symbol_findings() -> None:
    account, risk = _local_cross()
    eth = BinancePositionSnapshot("ETH-USDT-PERP", -1.0, 3_000.0, 5.0, "isolated", 600.0, 0.0, 600.0, 12.0)
    exchange = _exchange_cross(
        wallet_balance=900.0,
        positions=(
            BinancePositionSnapshot(
                "BTC-USDT-PERP",
                0.2,
                60_000.0,
                10.0,
                "isolated",
                610.0,
                100.0,
                610.0,
                24.4,
            ),
            eth,
        ),
    )

    record = build_binance_drift_evidence(
        account,
        risk,
        exchange,
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    assert record["has_drift"] is True
    assert record["severity"] == "drift"
    assert record["unexpected_on_exchange"] == ["ETH-USDT-PERP"]
    assert record["structural_differences"] == [
        "BTC-USDT-PERP:margin_mode:local=cross:exchange=isolated",
        "BTC-USDT-PERP:isolated_margin_presence",
    ]
    drifted = {(item["symbol"], item["field"]) for item in record["comparisons"] if not item["within_tolerance"]}
    assert (None, "wallet_balance") in drifted
    assert ("BTC-USDT-PERP", "quantity") in drifted


def test_complete_isolated_evidence_includes_position_collateral() -> None:
    account, risk, exchange = _local_isolated()

    record = build_binance_drift_evidence(
        account,
        risk,
        exchange,
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    assert record["status"] == "comparison_complete"
    assert record["has_drift"] is False
    isolated = next(item for item in record["comparisons"] if item["field"] == "isolated_margin")
    assert isolated["within_tolerance"] is True


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"schema_version": "tampered-schema"}, "schema_version"),
        ({"source_profile": "tampered-profile"}, "source_profile"),
        ({"configuration_hash": "not-a-sha256"}, "configuration_hash"),
        ({"fidelity_flags": ("client_observation_time",)}, "fidelity_flags"),
    ],
)
def test_complete_snapshot_with_unsupported_provenance_is_rejected(
    changes: dict[str, object],
    detail: str,
) -> None:
    account, risk = _local_cross()

    record = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(**changes),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    assert record["status"] == "comparison_rejected"
    assert record["has_drift"] is None
    assert record["rejection"]["code"] == "snapshot_unsupported_provenance"
    assert detail in record["rejection"]["details"]["invalid_fields"]


def test_unsupported_provenance_rejection_can_be_persisted(tmp_path) -> None:
    account, risk = _local_cross()
    record = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(fidelity_flags=("client_observation_time",)),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    jsonl_path, summary_path = write_binance_drift_evidence(tmp_path, record)

    assert json.loads(jsonl_path.read_text(encoding="utf-8"))["status"] == "comparison_rejected"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["rejection_code"] == "snapshot_unsupported_provenance"
    assert summary["has_drift"] is None


@pytest.mark.parametrize(
    ("snapshot", "expected_at", "code"),
    [
        (_exchange_cross(data_status="incomplete"), OBSERVED_AT, "snapshot_incomplete"),
        (_exchange_cross(data_status="unsupported"), OBSERVED_AT, "snapshot_unsupported"),
        (
            _exchange_cross(),
            pd.Timestamp("2026-08-27T08:00:05Z"),
            "timestamp_skew",
        ),
    ],
)
def test_rejected_observation_still_produces_fail_closed_evidence(
    snapshot: BinanceAccountSnapshot,
    expected_at: pd.Timestamp,
    code: str,
) -> None:
    account, risk = _local_cross()

    record = build_binance_drift_evidence(
        account,
        risk,
        snapshot,
        expected_timestamp=expected_at,
        tolerance=_tolerance(),
    )

    assert record["status"] == "comparison_rejected"
    assert record["severity"] == "rejected"
    assert record["has_drift"] is None
    assert record["comparisons"] == []
    assert record["rejection"]["code"] == code
    assert record["liquidation_engine_assessment"] == "not_assessed"
    assert build_binance_drift_summary(record)["rejection_code"] == code


def test_summary_is_concise_and_machine_readable() -> None:
    account, risk = _local_cross()
    record = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(wallet_balance=900.0),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    assert build_binance_drift_summary(record) == {
        "schema_version": "binance-usdm-drift-summary-v1",
        "latest_status": "comparison_complete",
        "severity": "drift",
        "has_drift": True,
        "observed_at": "2026-08-27T08:00:00+00:00",
        "source": "binance-usdm",
        "source_profile": "binance-live-sdk-readonly",
        "snapshot_schema_version": "binance-usdm-account-observation-v1",
        "snapshot_configuration_hash": "a" * 64,
        "tolerance": {
            "absolute": 0.01,
            "relative": 0.001,
            "max_timestamp_skew_seconds": 2.0,
            "version": "shadow-live-tolerance-v1",
        },
        "tolerance_version": "shadow-live-tolerance-v1",
        "comparison_count": 12,
        "out_of_tolerance_count": 1,
        "missing_symbol_count": 0,
        "unexpected_symbol_count": 0,
        "structural_difference_count": 0,
        "rejection_code": None,
        "fidelity_flags": [
            "conservative_intrabar_assumption",
            "client_observation_time",
            "sequential_signed_reads",
            "account_snapshot_comparison_only",
        ],
        "comparison_scope": "account_snapshot_fields_only",
        "liquidation_engine_assessment": "not_assessed",
    }


def test_writer_appends_jsonl_and_replaces_latest_summary(tmp_path) -> None:
    account, risk = _local_cross()
    record = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    latest = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(wallet_balance=900.0),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    paths = write_binance_drift_evidence(tmp_path, record)
    write_binance_drift_evidence(tmp_path, latest)

    jsonl_path = tmp_path / "artifacts" / "binance_drift.jsonl"
    summary_path = tmp_path / "artifacts" / "binance_drift_summary.json"
    assert paths == (jsonl_path, summary_path)
    assert [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()] == [
        record,
        latest,
    ]
    assert json.loads(summary_path.read_text(encoding="utf-8")) == build_binance_drift_summary(latest)
    assert summary_path.read_text(encoding="utf-8").endswith("\n")
    assert list(summary_path.parent.glob("*.tmp")) == []


def test_writer_rejects_non_finite_evidence_without_partial_artifact(tmp_path) -> None:
    account, risk = _local_cross()
    record = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )
    malformed = deepcopy(record)
    malformed["comparisons"][0]["local_value"] = float("nan")

    with pytest.raises(ValueError):
        write_binance_drift_evidence(tmp_path, malformed)

    assert not (tmp_path / "artifacts" / "binance_drift.jsonl").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "unknown-schema"),
        ("liquidation_engine_assessment", "validated"),
        ("comparison_scope", "liquidation_engine"),
    ],
)
def test_writer_rejects_tampered_scope_or_validation_claim(
    tmp_path,
    field: str,
    value: str,
) -> None:
    account, risk = _local_cross()
    record = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )
    record[field] = value

    with pytest.raises(ValueError):
        write_binance_drift_evidence(tmp_path, record)

    assert not (tmp_path / "artifacts" / "binance_drift.jsonl").exists()


def test_writer_rejects_missing_or_contradictory_evidence(tmp_path) -> None:
    account, risk = _local_cross()
    record = build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )
    malformed_records = []

    missing_tolerance = deepcopy(record)
    missing_tolerance["tolerance"] = {}
    malformed_records.append(missing_tolerance)

    malformed_flags = deepcopy(record)
    malformed_flags["fidelity_flags"] = "client_observation_time"
    malformed_records.append(malformed_flags)

    contradictory_comparison = deepcopy(record)
    contradictory_comparison["comparisons"][0]["within_tolerance"] = False
    malformed_records.append(contradictory_comparison)

    contradictory_symbols = deepcopy(record)
    contradictory_symbols["missing_on_exchange"] = ["ETH-USDT-PERP"]
    malformed_records.append(contradictory_symbols)

    tampered_source = deepcopy(record)
    tampered_source["source_profile"] = "live-order-profile"
    malformed_records.append(tampered_source)

    for malformed in malformed_records:
        with pytest.raises(ValueError):
            write_binance_drift_evidence(tmp_path, malformed)

    assert not (tmp_path / "artifacts" / "binance_drift.jsonl").exists()


def test_evidence_path_is_read_only_and_cannot_import_live_connectors() -> None:
    account, risk = _local_cross()
    before = (account, risk)

    build_binance_drift_evidence(
        account,
        risk,
        _exchange_cross(),
        expected_timestamp=OBSERVED_AT,
        tolerance=_tolerance(),
    )

    assert (account, risk) == before
    tree = ast.parse(inspect.getsource(evidence_module))
    forbidden_imports = (
        "aiohttp",
        "src.backtest.engines",
        "ccxt",
        "httpx",
        "requests",
        "socket",
        "src.markets.live",
        "src.markets.trading",
        "subprocess",
        "urllib",
        "websocket",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith(forbidden_imports) for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(forbidden_imports)
