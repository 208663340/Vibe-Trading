"""Deterministic Binance USD-M drift artifacts with no account action path."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

import pandas as pd

from src.backtest.binance_account_reconciliation import (
    BinanceAccountSnapshot,
    ReconciliationTolerance,
    reconcile_binance_account,
)
from src.backtest.perpetual_risk import AccountState, RiskSnapshot


EVIDENCE_SCHEMA_VERSION = "binance-usdm-drift-evidence-v1"
SUMMARY_SCHEMA_VERSION = "binance-usdm-drift-summary-v1"
SUPPORTED_SNAPSHOT_SCHEMA = "binance-usdm-account-observation-v1"
SUPPORTED_SOURCE_PROFILE = "binance-live-sdk-readonly"
REQUIRED_SNAPSHOT_FIDELITY_FLAGS = frozenset({"client_observation_time", "sequential_signed_reads"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EVIDENCE_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "severity",
        "has_drift",
        "expected_at",
        "observed_at",
        "source",
        "source_profile",
        "snapshot_schema_version",
        "snapshot_configuration_hash",
        "tolerance",
        "comparisons",
        "missing_on_exchange",
        "unexpected_on_exchange",
        "structural_differences",
        "fidelity_flags",
        "comparison_scope",
        "liquidation_engine_assessment",
        "rejection",
    }
)
_COMPARISON_FIELDS = frozenset(
    {
        "field",
        "local_value",
        "exchange_value",
        "absolute_delta",
        "allowed_delta",
        "within_tolerance",
        "symbol",
    }
)


def build_binance_drift_evidence(
    local_account: AccountState,
    local_risk: RiskSnapshot,
    exchange_snapshot: BinanceAccountSnapshot,
    *,
    expected_timestamp: pd.Timestamp,
    tolerance: ReconciliationTolerance = ReconciliationTolerance(),
) -> dict[str, Any]:
    """Build one comparison or fail-closed rejection record.

    The returned record is evidence only. It never updates local accounting or
    implies that Binance liquidation behavior has been validated.
    """
    expected = _utc_timestamp(expected_timestamp, "expected_timestamp")
    observed = _utc_timestamp(exchange_snapshot.observed_at, "observed_at")
    flags = list(
        dict.fromkeys(
            (
                *local_risk.fidelity_flags,
                *exchange_snapshot.fidelity_flags,
                "account_snapshot_comparison_only",
            )
        )
    )
    record: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "status": "comparison_rejected",
        "severity": "rejected",
        "has_drift": None,
        "expected_at": expected.isoformat(),
        "observed_at": observed.isoformat(),
        "source": exchange_snapshot.source,
        "source_profile": exchange_snapshot.source_profile,
        "snapshot_schema_version": exchange_snapshot.schema_version,
        "snapshot_configuration_hash": exchange_snapshot.configuration_hash,
        "tolerance": {
            "absolute": tolerance.absolute,
            "relative": tolerance.relative,
            "max_timestamp_skew_seconds": tolerance.max_timestamp_skew_seconds,
            "version": tolerance.version,
        },
        "comparisons": [],
        "missing_on_exchange": [],
        "unexpected_on_exchange": [],
        "structural_differences": [],
        "fidelity_flags": flags,
        "comparison_scope": "account_snapshot_fields_only",
        "liquidation_engine_assessment": "not_assessed",
        "rejection": None,
    }

    if exchange_snapshot.data_status != "complete":
        record["rejection"] = {
            "code": f"snapshot_{exchange_snapshot.data_status}",
            "message": "exchange snapshot data_status must be complete",
            "details": {"data_status": exchange_snapshot.data_status},
        }
        return record

    invalid_provenance = _unsupported_snapshot_provenance(exchange_snapshot)
    if invalid_provenance:
        record["rejection"] = {
            "code": "snapshot_unsupported_provenance",
            "message": "exchange snapshot provenance is unsupported",
            "details": {"invalid_fields": invalid_provenance},
        }
        return record

    skew_seconds = abs((observed - expected).total_seconds())
    if skew_seconds > tolerance.max_timestamp_skew_seconds:
        record["rejection"] = {
            "code": "timestamp_skew",
            "message": "exchange snapshot timestamp skew exceeds tolerance",
            "details": {
                "timestamp_skew_seconds": skew_seconds,
                "max_timestamp_skew_seconds": tolerance.max_timestamp_skew_seconds,
            },
        }
        return record

    report = reconcile_binance_account(
        local_account,
        local_risk,
        exchange_snapshot,
        expected_timestamp=expected,
        tolerance=tolerance,
    )
    record.update(
        {
            "status": report.status,
            "severity": "drift" if report.has_drift else "none",
            "has_drift": report.has_drift,
            "comparisons": [asdict(item) for item in report.comparisons],
            "missing_on_exchange": list(report.missing_on_exchange),
            "unexpected_on_exchange": list(report.unexpected_on_exchange),
            "structural_differences": list(report.structural_differences),
            "fidelity_flags": list(report.fidelity_flags),
            "comparison_scope": report.comparison_scope,
            "liquidation_engine_assessment": report.liquidation_engine_assessment,
        }
    )
    return record


def build_binance_drift_summary(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Build the concise latest-observation summary for one evidence record."""
    _validate_evidence(evidence)
    comparisons = list(evidence["comparisons"])
    rejection = evidence["rejection"]
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "latest_status": evidence["status"],
        "severity": evidence["severity"],
        "has_drift": evidence["has_drift"],
        "observed_at": evidence["observed_at"],
        "source": evidence["source"],
        "source_profile": evidence["source_profile"],
        "snapshot_schema_version": evidence["snapshot_schema_version"],
        "snapshot_configuration_hash": evidence["snapshot_configuration_hash"],
        "tolerance": dict(evidence["tolerance"]),
        "tolerance_version": evidence["tolerance"]["version"],
        "comparison_count": len(comparisons),
        "out_of_tolerance_count": sum(not item["within_tolerance"] for item in comparisons),
        "missing_symbol_count": len(evidence["missing_on_exchange"]),
        "unexpected_symbol_count": len(evidence["unexpected_on_exchange"]),
        "structural_difference_count": len(evidence["structural_differences"]),
        "rejection_code": (rejection.get("code") if isinstance(rejection, Mapping) else None),
        "fidelity_flags": list(evidence["fidelity_flags"]),
        "comparison_scope": evidence["comparison_scope"],
        "liquidation_engine_assessment": evidence["liquidation_engine_assessment"],
    }


def write_binance_drift_evidence(
    run_dir: Path | str,
    evidence: Mapping[str, Any],
) -> tuple[Path, Path]:
    """Append one strict JSONL record and replace its latest summary."""
    summary = build_binance_drift_summary(evidence)
    evidence_json = json.dumps(
        evidence,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    summary_json = json.dumps(
        summary,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    )

    artifact_dir = Path(run_dir) / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = artifact_dir / "binance_drift.jsonl"
    summary_path = artifact_dir / "binance_drift_summary.json"
    with jsonl_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(evidence_json + "\n")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=artifact_dir,
            prefix=f".{summary_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(summary_json + "\n")
            temp_path = Path(handle.name)
        temp_path.replace(summary_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return jsonl_path, summary_path


def _utc_timestamp(value: pd.Timestamp, name: str) -> pd.Timestamp:
    if not isinstance(value, pd.Timestamp) or pd.isna(value) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware pandas Timestamp")
    return value.tz_convert("UTC")


def _unsupported_snapshot_provenance(snapshot: BinanceAccountSnapshot) -> list[str]:
    invalid: list[str] = []
    if snapshot.schema_version != SUPPORTED_SNAPSHOT_SCHEMA:
        invalid.append("schema_version")
    if snapshot.source_profile != SUPPORTED_SOURCE_PROFILE:
        invalid.append("source_profile")
    if _SHA256.fullmatch(snapshot.configuration_hash) is None:
        invalid.append("configuration_hash")
    if not REQUIRED_SNAPSHOT_FIDELITY_FLAGS.issubset(snapshot.fidelity_flags):
        invalid.append("fidelity_flags")
    return invalid


def _validate_evidence(evidence: Mapping[str, Any]) -> None:
    if set(evidence) != _EVIDENCE_FIELDS:
        raise ValueError("Binance drift evidence fields do not match its schema")
    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise ValueError("unsupported Binance drift evidence schema")
    if evidence.get("comparison_scope") != "account_snapshot_fields_only":
        raise ValueError("Binance drift evidence comparison scope is unsupported")
    if evidence.get("liquidation_engine_assessment") != "not_assessed":
        raise ValueError("Binance drift evidence cannot assess the liquidation engine")

    _validate_timestamp_text(evidence["expected_at"], "expected_at")
    _validate_timestamp_text(evidence["observed_at"], "observed_at")
    if evidence["source"] != "binance-usdm":
        raise ValueError("Binance drift evidence source is unsupported")
    for field in (
        "source_profile",
        "snapshot_schema_version",
        "snapshot_configuration_hash",
    ):
        if not isinstance(evidence[field], str) or not evidence[field]:
            raise ValueError(f"Binance drift evidence {field} must be a non-empty string")

    tolerance = evidence["tolerance"]
    if not isinstance(tolerance, Mapping) or set(tolerance) != {
        "absolute",
        "relative",
        "max_timestamp_skew_seconds",
        "version",
    }:
        raise ValueError("Binance drift evidence tolerance is malformed")
    for field in ("absolute", "relative", "max_timestamp_skew_seconds"):
        _validate_number(tolerance[field], f"tolerance.{field}", non_negative=True)
    if not isinstance(tolerance["version"], str) or not tolerance["version"]:
        raise ValueError("Binance drift evidence tolerance version is malformed")

    comparisons = evidence["comparisons"]
    if not isinstance(comparisons, list):
        raise ValueError("Binance drift evidence comparisons must be a list")
    for item in comparisons:
        _validate_comparison(item)
    for field in ("missing_on_exchange", "unexpected_on_exchange", "structural_differences"):
        _validate_string_list(evidence[field], field)
    _validate_string_list(evidence["fidelity_flags"], "fidelity_flags")
    required_flags = {"account_snapshot_comparison_only"}
    if evidence["status"] == "comparison_complete":
        required_flags.update(REQUIRED_SNAPSHOT_FIDELITY_FLAGS)
    if not required_flags.issubset(evidence["fidelity_flags"]):
        raise ValueError("Binance drift evidence is missing required fidelity flags")

    status = evidence.get("status")
    severity = evidence.get("severity")
    has_drift = evidence.get("has_drift")
    rejection = evidence.get("rejection")
    if status == "comparison_complete":
        if (
            evidence["snapshot_schema_version"] != SUPPORTED_SNAPSHOT_SCHEMA
            or evidence["source_profile"] != SUPPORTED_SOURCE_PROFILE
            or _SHA256.fullmatch(evidence["snapshot_configuration_hash"]) is None
        ):
            raise ValueError("completed Binance drift evidence has unsupported provenance")
        derived_drift = bool(
            evidence["missing_on_exchange"]
            or evidence["unexpected_on_exchange"]
            or evidence["structural_differences"]
            or any(not item["within_tolerance"] for item in comparisons)
        )
        expected_severity = "drift" if derived_drift else "none"
        if has_drift is not derived_drift or severity != expected_severity or rejection is not None:
            raise ValueError("completed Binance drift evidence is internally inconsistent")
    elif status == "comparison_rejected":
        if (
            has_drift is not None
            or severity != "rejected"
            or not isinstance(rejection, Mapping)
            or set(rejection) != {"code", "message", "details"}
            or not isinstance(rejection["code"], str)
            or not rejection["code"]
            or not isinstance(rejection["message"], str)
            or not rejection["message"]
            or not isinstance(rejection["details"], Mapping)
            or comparisons
            or evidence["missing_on_exchange"]
            or evidence["unexpected_on_exchange"]
            or evidence["structural_differences"]
        ):
            raise ValueError("rejected Binance drift evidence is internally inconsistent")
    else:
        raise ValueError("unsupported Binance drift evidence status")


def _validate_timestamp_text(value: Any, name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"Binance drift evidence {name} must be an ISO timestamp")
    try:
        _utc_timestamp(pd.Timestamp(value), name)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Binance drift evidence {name} must be an ISO timestamp") from exc


def _validate_number(value: Any, name: str, *, non_negative: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Binance drift evidence {name} must be finite")
    if non_negative and value < 0:
        raise ValueError(f"Binance drift evidence {name} must be non-negative")


def _validate_string_list(value: Any, name: str) -> None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError(f"Binance drift evidence {name} must contain unique strings")


def _validate_comparison(item: Any) -> None:
    if not isinstance(item, Mapping) or set(item) != _COMPARISON_FIELDS:
        raise ValueError("Binance drift evidence comparison is malformed")
    if not isinstance(item["field"], str) or not item["field"]:
        raise ValueError("Binance drift evidence comparison field is malformed")
    for field in ("local_value", "exchange_value", "absolute_delta", "allowed_delta"):
        _validate_number(
            item[field],
            f"comparison.{field}",
            non_negative=field in {"absolute_delta", "allowed_delta"},
        )
    if not isinstance(item["within_tolerance"], bool):
        raise ValueError("Binance drift evidence comparison tolerance result is malformed")
    if item["symbol"] is not None and (not isinstance(item["symbol"], str) or not item["symbol"]):
        raise ValueError("Binance drift evidence comparison symbol is malformed")
    if not math.isclose(
        item["absolute_delta"],
        abs(item["local_value"] - item["exchange_value"]),
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise ValueError("Binance drift evidence comparison delta is inconsistent")
    if item["within_tolerance"] is not (item["absolute_delta"] <= item["allowed_delta"]):
        raise ValueError("Binance drift evidence comparison tolerance result is inconsistent")
