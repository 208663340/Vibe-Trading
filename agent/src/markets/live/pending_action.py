"""Crash-safe ownership marker for one unresolved broker side effect."""

from __future__ import annotations

import json
import math
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Mapping, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.markets.live.paths import broker_dir

_FILENAME = "pending_action.json"


class PendingActionError(RuntimeError):
    """Raised when pending state cannot be trusted or durably changed."""


class PendingOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    symbol: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    quantity: float | int | None
    notional: float | int | None
    order_type: Literal["market", "limit"]
    limit_price: float | int | None
    time_in_force: Literal["day", "gtc"]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if (self.quantity is None) == (self.notional is None):
            raise ValueError("exactly one order size is required")
        for value in (self.quantity, self.notional, self.limit_price):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError("order numerics must be finite and positive")
        if (self.order_type == "limit") != (self.limit_price is not None):
            raise ValueError("limit price must match the order type")
        return self


class RecoveredOrderEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    broker_order_id: str = Field(min_length=1)
    client_order_id: str = Field(min_length=1, max_length=48)
    symbol: str = Field(min_length=1)
    side: Literal["buy", "sell"]
    order_type: Literal["market", "limit"]
    time_in_force: Literal["day", "gtc"]
    quantity: str | float | int | None
    notional: str | float | int | None
    limit_price: str | float | int | None
    filled_qty: str | float | int
    order_status: str = Field(min_length=1)
    submitted_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_order_evidence(self) -> Self:
        try:
            quantity = (
                Decimal(str(self.quantity)) if self.quantity is not None else None
            )
            notional = (
                Decimal(str(self.notional)) if self.notional is not None else None
            )
            limit_price = (
                Decimal(str(self.limit_price)) if self.limit_price is not None else None
            )
            filled = Decimal(str(self.filled_qty))
            submitted = datetime.fromisoformat(self.submitted_at.replace("Z", "+00:00"))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("recovered order evidence is malformed") from exc
        numerics = [
            value
            for value in (quantity, notional, limit_price, filled)
            if value is not None
        ]
        supported = {
            "accepted",
            "new",
            "open",
            "pending_new",
            "accepted_for_bidding",
            "rejected",
            "canceled",
            "expired",
            "partially_filled",
            "filled",
        }
        working = {"accepted", "new", "open", "pending_new", "accepted_for_bidding"}
        fill_statuses = {"partially_filled", "filled"}
        if (
            (quantity is None) == (notional is None)
            or any(not value.is_finite() for value in numerics)
            or any(value <= 0 for value in (quantity, notional) if value is not None)
            or filled < 0
            or (self.order_type == "limit") != (limit_price is not None)
            or (limit_price is not None and limit_price <= 0)
            or submitted.tzinfo is None
            or self.order_status not in supported
            or (filled == 0 and self.order_status in fill_statuses)
            or (filled > 0 and self.order_status in working | {"rejected"})
            or (quantity is not None and filled > quantity)
        ):
            raise ValueError("recovered order evidence is contradictory")
        return self


class RecoveredPositionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pre_position_qty: str
    current_position_qty: str
    attributed_filled_qty: str

    @model_validator(mode="after")
    def validate_quantities(self) -> Self:
        try:
            values = [
                Decimal(value)
                for value in (
                    self.pre_position_qty,
                    self.current_position_qty,
                    self.attributed_filled_qty,
                )
            ]
        except InvalidOperation as exc:
            raise ValueError("position evidence must be exact decimals") from exc
        if any(not value.is_finite() for value in values) or values[2] <= 0:
            raise ValueError("position evidence must be finite with a positive fill")
        return self


class PendingAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    broker: Literal["alpaca"]
    action_id: str = Field(pattern=r"^act_[0-9a-f]{32}$")
    phase: Literal[
        "pending_write", "resolved_fill_pending_audit", "resolved_needs_revalidation"
    ]
    kind: Literal["place_order"]
    created_at: datetime
    client_order_id: str = Field(min_length=1, max_length=48)
    broker_order_id: str | None = None
    request: PendingOrderRequest
    pre_position_qty: str | float | int | None
    remediation_attempts: Literal[0]
    resolution: RecoveredOrderEvidence | None = None
    position_resolution: RecoveredPositionEvidence | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at requires a timezone")
        if self.pre_position_qty is not None:
            try:
                pre_position = Decimal(str(self.pre_position_qty))
            except InvalidOperation as exc:
                raise ValueError("pre-position quantity must be exact") from exc
            if not pre_position.is_finite():
                raise ValueError("pre-position quantity must be finite")
        if self.phase == "pending_write" and (
            self.broker_order_id is not None
            or self.resolution is not None
            or self.position_resolution is not None
        ):
            raise ValueError("pending write cannot contain resolved evidence")
        if self.phase != "pending_write" and (
            self.resolution is None
            or self.broker_order_id != self.resolution.broker_order_id
            or self.client_order_id != self.resolution.client_order_id
        ):
            raise ValueError("resolved phase requires matching exact evidence")
        if self.resolution is not None:
            request = self.request
            resolution = self.resolution
            numeric_pairs = (
                (resolution.quantity, request.quantity),
                (resolution.notional, request.notional),
                (resolution.limit_price, request.limit_price),
            )
            if (
                resolution.symbol != request.symbol
                or resolution.side != request.side
                or resolution.order_type != request.order_type
                or resolution.time_in_force != request.time_in_force
                or any(
                    actual is None
                    or expected is None
                    or Decimal(str(actual)) != Decimal(str(expected))
                    for actual, expected in numeric_pairs
                    if actual is not None or expected is not None
                )
            ):
                raise ValueError("resolved evidence contradicts the owned request")
        if (
            self.phase == "resolved_fill_pending_audit"
            and self.position_resolution is None
        ):
            raise ValueError("fill resolution requires exact position evidence")
        if self.position_resolution is not None:
            if self.resolution is None or self.pre_position_qty is None:
                raise ValueError(
                    "position resolution requires order and pre-position evidence"
                )
            before = Decimal(self.position_resolution.pre_position_qty)
            after = Decimal(self.position_resolution.current_position_qty)
            attributed = Decimal(self.position_resolution.attributed_filled_qty)
            filled = Decimal(str(self.resolution.filled_qty))
            expected = (
                before + attributed
                if self.request.side == "buy"
                else before - attributed
            )
            if (
                before != Decimal(str(self.pre_position_qty))
                or attributed != filled
                or after != expected
            ):
                raise ValueError("position resolution contradicts the owned order")
        return self


def new_pending_order(
    broker: str,
    request: Mapping[str, object],
    *,
    pre_position_qty: str | float | int | None,
) -> PendingAction:
    """Build the redaction-safe marker written immediately before submission."""
    key = _broker_key(broker)
    return PendingAction(
        schema_version=1,
        broker=key,
        action_id=f"act_{uuid.uuid4().hex}",
        phase="pending_write",
        kind="place_order",
        created_at=datetime.now(timezone.utc),
        client_order_id=f"vt-{uuid.uuid4().hex}",
        request=PendingOrderRequest(
            symbol=str(request.get("symbol") or "").strip().upper(),
            side=str(request.get("side") or "").strip().lower(),
            quantity=request.get("quantity"),
            notional=request.get("notional"),
            order_type=str(request.get("order_type") or "market").strip().lower(),
            limit_price=request.get("limit_price"),
            time_in_force=str(request.get("time_in_force") or "day").strip().lower(),
        ),
        pre_position_qty=pre_position_qty,
        remediation_attempts=0,
    )


def pending_action_path(broker: str) -> Path:
    return broker_dir(_broker_key(broker)) / _FILENAME


def load_pending_action(broker: str) -> PendingAction | None:
    """Load strict pending evidence; malformed state raises and fails closed."""
    path = pending_action_path(broker)
    try:
        payload = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PendingActionError("pending action cannot be read") from exc
    try:
        return PendingAction.model_validate_json(payload)
    except ValidationError as exc:
        raise PendingActionError("pending action has an invalid schema") from exc


def save_pending_action(action: PendingAction) -> None:
    """Durably create pending evidence without replacing an existing marker."""
    path = pending_action_path(action.broker)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() or path.is_symlink():
        raise PendingActionError("a pending broker action already exists")
    _write_action(path, action)


def transition_to_revalidation(
    action: PendingAction, evidence: Mapping[str, object]
) -> PendingAction:
    """Durably retain exact working-order truth for later policy revalidation."""
    return _transition(action, evidence, "resolved_needs_revalidation")


def transition_to_fill_resolution(
    action: PendingAction,
    evidence: Mapping[str, object],
    position_evidence: Mapping[str, object],
) -> PendingAction:
    """Durably retain an attributed fill before its audit/terminal transition."""
    return _transition(
        action,
        evidence,
        "resolved_fill_pending_audit",
        position_evidence=position_evidence,
    )


def _transition(
    action: PendingAction,
    evidence: Mapping[str, object],
    phase: Literal["resolved_fill_pending_audit", "resolved_needs_revalidation"],
    *,
    position_evidence: Mapping[str, object] | None = None,
) -> PendingAction:
    resolution = RecoveredOrderEvidence.model_validate(evidence)
    position_resolution = (
        RecoveredPositionEvidence.model_validate(position_evidence)
        if position_evidence is not None
        else action.position_resolution
    )
    updated = PendingAction.model_validate(
        {
            **action.model_dump(mode="python"),
            "phase": phase,
            "broker_order_id": resolution.broker_order_id,
            "resolution": resolution,
            "position_resolution": position_resolution,
        }
    )
    current = load_pending_action(action.broker)
    if current is None or current.action_id != action.action_id:
        raise PendingActionError("pending action changed before transition")
    _write_action(pending_action_path(action.broker), updated)
    return updated


def _write_action(path: Path, action: PendingAction) -> None:
    payload = json.dumps(
        action.model_dump(mode="json"),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def clear_pending_action(broker: str, action_id: str) -> None:
    """Durably remove exactly the marker whose resolution was audited."""
    action = load_pending_action(broker)
    if action is None or action.action_id != action_id:
        raise PendingActionError("pending action changed before clear")
    path = pending_action_path(broker)
    path.unlink()
    _fsync_directory(path.parent)


def _broker_key(broker: str) -> str:
    key = str(broker or "").strip().lower()
    if key != "alpaca":
        raise ValueError("pending submit ownership currently supports Alpaca only")
    return key


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":  # Windows has no portable directory fsync.
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
