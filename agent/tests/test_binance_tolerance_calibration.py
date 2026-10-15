"""Deterministic tolerance calibration from recorded reconciliation samples."""

from __future__ import annotations

import pandas as pd
import pytest

from src.backtest.binance_account_reconciliation import (
    BinanceAccountSnapshot,
    BinancePositionSnapshot,
    ReconciliationTolerance,
    reconcile_binance_account,
)
from src.backtest.binance_tolerance_calibration import (
    CalibrationSample,
    calibrate_tolerance,
    verify_tolerance_covers,
)
from src.backtest.perpetual_risk import (
    AccountState,
    PositionRisk,
    PositionState,
    RiskSnapshot,
)


OBSERVED_AT = pd.Timestamp("2026-08-27T08:00:00Z")


def _samples() -> list[CalibrationSample]:
    return [
        CalibrationSample("entry_price", 60_000.0, 60_001.0, "BTC-USDT-PERP"),
        CalibrationSample("entry_price", 60_000.0, 60_000.5, "BTC-USDT-PERP"),
        CalibrationSample("entry_price", 60_000.0, 59_999.0, "BTC-USDT-PERP"),
        CalibrationSample("entry_price", 60_000.0, 60_000.25, "BTC-USDT-PERP"),
        CalibrationSample("quantity", 0.1, 0.1003, "BTC-USDT-PERP"),
        CalibrationSample("quantity", 0.1, 0.1001, "BTC-USDT-PERP"),
        CalibrationSample("quantity", 0.1, 0.0999, "BTC-USDT-PERP"),
        CalibrationSample("quantity", 0.1, 0.1002, "BTC-USDT-PERP"),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.4),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.2),
        CalibrationSample("wallet_balance", 1_000.0, 999.7),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.1),
    ]


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


def test_calibration_covers_recorded_samples() -> None:
    samples = _samples()

    calibration = calibrate_tolerance(samples, version="calibration-v1")

    assert isinstance(calibration.tolerance, ReconciliationTolerance)
    assert verify_tolerance_covers(calibration.tolerance, samples) == ()


def test_zero_drift_samples_get_positive_floors() -> None:
    samples = [CalibrationSample("quantity", 0.1, 0.1, "BTC-USDT-PERP") for _ in range(5)]

    calibration = calibrate_tolerance(samples, version="calibration-v1", absolute_floor=1e-6)

    field = calibration.fields[0]
    assert field.max_absolute_delta == 0.0
    assert field.proposed_absolute == 1e-6
    assert field.proposed_relative == 1e-8
    assert calibration.tolerance.absolute == 1e-6 > 0
    assert verify_tolerance_covers(calibration.tolerance, samples) == ()


def test_field_stats_and_ordering() -> None:
    samples = [
        CalibrationSample("available_balance", 500.0, 500.25),
        CalibrationSample("available_balance", 500.0, 499.5),
        CalibrationSample("available_balance", 500.0, 500.0),
        CalibrationSample("available_balance", 500.0, 500.1),
        CalibrationSample("wallet_balance", 1_000.0, 1_001.0),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.5),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.25),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.1),
    ]

    calibration = calibrate_tolerance(samples, version="calibration-v1")

    assert calibration.version == "calibration-v1"
    assert calibration.sample_count == 8
    assert [item.field for item in calibration.fields] == ["available_balance", "wallet_balance"]
    available = calibration.fields[0]
    assert available.sample_count == 4
    assert available.max_absolute_delta == 0.5
    assert available.proposed_absolute == pytest.approx(1.0)
    assert available.proposed_relative == pytest.approx(0.002)
    wallet = calibration.fields[1]
    assert wallet.sample_count == 4
    assert wallet.max_absolute_delta == 1.0
    assert wallet.proposed_absolute == pytest.approx(2.0)
    assert wallet.proposed_relative == pytest.approx(2.0 / 1_001.0)
    assert calibration.tolerance.absolute == pytest.approx(2.0)
    assert calibration.tolerance.relative == pytest.approx(0.002)
    assert calibration.tolerance.max_timestamp_skew_seconds == 0.0
    assert calibration.tolerance.version == "calibration-v1"


def test_calibration_accepted_by_reconciler() -> None:
    position = PositionState("BTC-USDT-PERP", 0.1, 60_000.0, 10.0, 3.0, None)
    account = AccountState(1_000.0, (position,), "cross")
    risk = PositionRisk("BTC-USDT-PERP", 61_000.0, 6_100.0, 100.0, 610.0, 24.4, None)
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
    exchange = _exchange_cross(wallet_balance=1_000.4)
    samples = [
        CalibrationSample("wallet_balance", 1_000.0, 1_000.4),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.3),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.2),
        CalibrationSample("wallet_balance", 1_000.0, 1_000.1),
    ]

    calibration = calibrate_tolerance(samples, version="calibration-v1")

    assert verify_tolerance_covers(calibration.tolerance, samples) == ()
    report = reconcile_binance_account(
        account,
        snapshot,
        exchange,
        expected_timestamp=OBSERVED_AT,
        tolerance=calibration.tolerance,
    )
    assert report.has_drift is False


@pytest.mark.parametrize(
    "calibrate",
    [
        lambda: calibrate_tolerance([], version="calibration-v1"),
        lambda: calibrate_tolerance(_samples(), version=""),
        lambda: calibrate_tolerance(_samples(), version="calibration-v1", safety_factor=0.5),
        lambda: calibrate_tolerance(_samples(), version="calibration-v1", min_samples_per_field=5),
        lambda: calibrate_tolerance(_samples(), version="calibration-v1", absolute_floor=-1.0),
        lambda: calibrate_tolerance(_samples(), version="calibration-v1", relative_floor=-1.0),
        lambda: calibrate_tolerance(_samples(), version="calibration-v1", absolute_floor=float("nan")),
        lambda: calibrate_tolerance(_samples(), version="calibration-v1", min_samples_per_field=0),
    ],
)
def test_calibration_fails_closed(calibrate) -> None:
    with pytest.raises(ValueError):
        calibrate()


@pytest.mark.parametrize(
    "build_sample",
    [
        lambda: CalibrationSample("wallet_balance", float("nan"), 1_000.0),
        lambda: CalibrationSample("wallet_balance", 1_000.0, float("inf")),
        lambda: CalibrationSample("", 1_000.0, 1_000.0),
        lambda: CalibrationSample("quantity", 1.0, 1.0, "BTCUSDT"),
        lambda: CalibrationSample("quantity", 1.0, 1.0, "btc-usdt-perp"),
    ],
)
def test_sample_fails_closed(build_sample) -> None:
    with pytest.raises(ValueError):
        build_sample()


def test_infinite_safety_factor_rejected() -> None:
    with pytest.raises(ValueError):
        calibrate_tolerance(_samples(), version="calibration-v1", safety_factor=float("inf"))


def test_boundary_delta_is_covered() -> None:
    tolerance = ReconciliationTolerance(
        absolute=0.5,
        relative=0.0,
        max_timestamp_skew_seconds=0.0,
        version="reconciliation-tolerance-v1",
    )
    boundary = CalibrationSample("wallet_balance", 1.0, 0.5)
    offending = CalibrationSample("wallet_balance", 1.0, 0.4)

    assert verify_tolerance_covers(tolerance, [boundary]) == ()
    assert verify_tolerance_covers(tolerance, [boundary, offending]) == (offending,)


def test_verify_flags_uncovered_sample() -> None:
    covered = CalibrationSample("quantity", 0.1, 0.1, "BTC-USDT-PERP")
    offending = CalibrationSample("quantity", 0.1, 0.2, "BTC-USDT-PERP")
    tight = ReconciliationTolerance(absolute=1e-3, relative=1e-3, version="tight-v1")

    uncovered = verify_tolerance_covers(tight, [covered, offending])

    assert uncovered == (offending,)
    assert verify_tolerance_covers(tight, []) == ()
