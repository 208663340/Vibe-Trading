"""Live action gate tests for eToro-specific writes."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext

import pytest

from src.markets.live import sdk_order_gate as gate
from src.markets.live.enforcement import OrderIntent
from src.markets.live.mandate.model import AssetClass, ConsentMeta, HardCaps, InstrumentType, Mandate, UniverseConstraint
from src.markets.trading import service

pytestmark = pytest.mark.unit


class _FakeEtoroModule:
    def __init__(self, *, account_currency: str = "USD"):
        self.calls: list[str] = []
        self.close_kwargs: dict = {}
        self.account_currency = account_currency

    def build_config(self, profile_config, overrides):
        return None

    def close_position(self, config, **kwargs):
        self.calls.append("close")
        self.close_kwargs = dict(kwargs)
        return {"status": "ok", "position_id": kwargs.get("position_id")}

    def cancel_close_order(self, config, order_id="", *, request_id=None, **kwargs):
        self.calls.append("cancel_close")
        return {"status": "ok", "order_id": order_id}

    def edit_position_stops(self, config, **kwargs):
        self.calls.append("edit")
        return {"status": "ok", "position_id": kwargs.get("position_id")}

    def copy_start_or_adjust(self, config, **kwargs):
        self.calls.append("copy_start")
        return {"status": "ok", "reference_id": "ref-1"}

    def copy_close(self, config, **kwargs):
        self.calls.append("copy_close")
        return {"status": "ok", "mirror_id": kwargs.get("mirror_id")}

    def cancel_order(self, config, order_id, **kwargs):
        self.calls.append("cancel")
        return {"status": "ok", "order_id": order_id}

    def get_positions(self, config):
        return {
            "status": "ok",
            "positions": [
                {
                    "position_id": 99,
                    "instrument_id": 100000,
                    "symbol": "AAPL",
                    "units": 10.0,
                }
            ],
        }

    def get_account_snapshot(self, config):
        return {
            "status": "ok",
            "account": {"pnl": {"account_currency": self.account_currency}},
        }


def _mandate():
    return Mandate(
        schema_version=1,
        hard_caps=HardCaps(
            account_funding_usd=1_000_000.0,
            max_order_notional_usd=1_000_000.0,
            max_total_exposure_usd=1_000_000.0,
            max_leverage=2.0,
            allowed_instruments=(InstrumentType.EQUITY,),
            max_trades_per_day=100,
        ),
        universe=UniverseConstraint(
            asset_classes=(AssetClass.US_EQUITY,),
            min_market_cap_usd=None,
            min_avg_daily_volume_usd=None,
            exclude_symbols=(),
        ),
        consent=ConsentMeta(
            created_at="2026-01-01T00:00:00+00:00",
            consent_token_sha256="deadbeef",
            broker="etoro",
            account_ref="acct-1",
            expires_at="2999-01-01T00:00:00+00:00",
        ),
    )


def _patch_gate(monkeypatch, *, mandate, halted=False):
    monkeypatch.setattr(gate, "load_mandate", lambda broker: mandate)
    monkeypatch.setattr(gate, "halt_flag_set", lambda broker: halted)
    monkeypatch.setattr(gate, "write_live_action", lambda *a, **k: {"audited": True})
    monkeypatch.setattr(gate, "read_daily_count", lambda broker: 0)
    monkeypatch.setattr(gate, "increment_daily_count", lambda broker: 1)
    monkeypatch.setattr(gate, "daily_order_lock", lambda broker: nullcontext())


def test_risk_reducing_close_allowed_when_halted(monkeypatch) -> None:
    module = _FakeEtoroModule()
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)
    result = gate.execute_live_action(
        broker="etoro",
        connector_module=module,
        config=None,
        remote_tool="close_position",
        risk_reducing=True,
        intent=None,
        execute_fn=lambda: module.close_position(None, position_id="123"),
        audit_request={"position_id": "123"},
    )
    assert result["status"] == "ok"
    assert module.calls == ["close"]


def test_risk_increasing_edit_requires_mandate_when_halted(monkeypatch) -> None:
    module = _FakeEtoroModule()
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)
    intent = OrderIntent(
        symbol="POS:1",
        side="sell",
        notional_usd=0.0,
        quantity=None,
        instrument_type=InstrumentType.EQUITY,
        asset_class=AssetClass.US_EQUITY,
    )
    result = gate.execute_live_action(
        broker="etoro",
        connector_module=module,
        config=None,
        remote_tool="edit_position_stops",
        risk_reducing=False,
        intent=intent,
        execute_fn=lambda: {"status": "ok"},
        audit_request={"position_id": "1"},
    )
    assert result["status"] == "blocked"


def test_risk_increasing_action_executes_and_counts_inside_daily_lock(
    monkeypatch,
) -> None:
    module = _FakeEtoroModule()
    state = {"inside": False, "executed": False, "incremented": False}
    _patch_gate(monkeypatch, mandate=_mandate())
    monkeypatch.setattr(gate, "check_mandate", lambda *args, **kwargs: None)

    @contextmanager
    def _lock(_broker):
        state["inside"] = True
        try:
            yield
        finally:
            state["inside"] = False

    def _execute():
        assert state["inside"] is True
        state["executed"] = True
        return {"status": "ok"}

    def _increment(_broker):
        assert state["inside"] is True
        assert state["executed"] is True
        state["incremented"] = True
        return 1

    monkeypatch.setattr(gate, "daily_order_lock", _lock)
    monkeypatch.setattr(gate, "increment_daily_count", _increment)
    intent = OrderIntent(
        symbol="AAPL",
        side="buy",
        notional_usd=100.0,
        quantity=None,
        instrument_type=InstrumentType.EQUITY,
        asset_class=AssetClass.US_EQUITY,
    )

    result = gate.execute_live_action(
        broker="etoro",
        connector_module=module,
        config=None,
        remote_tool="copy_start_or_adjust",
        risk_reducing=False,
        intent=intent,
        execute_fn=_execute,
    )

    assert result["status"] == "ok"
    assert state == {"inside": False, "executed": True, "incremented": True}


def test_copy_close_allowed_when_halted(monkeypatch) -> None:
    module = _FakeEtoroModule()
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)
    result = gate.execute_live_action(
        broker="etoro",
        connector_module=module,
        config=None,
        remote_tool="copy_close",
        risk_reducing=True,
        intent=None,
        execute_fn=lambda: module.copy_close(None, mirror_id=1),
        audit_request={"mirror_id": 1},
    )
    assert result["status"] == "ok"
    assert module.calls == ["copy_close"]


def test_service_close_position_paper_bypasses_gate(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    result = service.close_position("99", "etoro-paper-trade")
    assert result["status"] == "ok"
    assert module.calls == ["close"]
    assert module.close_kwargs["instrument_id"] == 100000


def test_service_partial_close_is_validated_and_risk_reducing_when_halted(
    monkeypatch,
) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    _patch_gate(monkeypatch, mandate=_mandate(), halted=True)

    result = service.close_position(
        "99",
        "etoro-live-trade",
        units_to_close=4.0,
    )

    assert result["status"] == "ok"
    assert module.calls == ["close"]
    assert module.close_kwargs["instrument_id"] == 100000
    assert module.close_kwargs["units_to_close"] == 4.0


def test_service_partial_close_refuses_units_above_open_position(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)

    result = service.close_position(
        "99",
        "etoro-paper-trade",
        units_to_close=11.0,
    )

    assert result["status"] == "error"
    assert "exceeds open position units" in result["error"]
    assert module.calls == []


def test_service_cancel_close_order_paper_bypasses_gate(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    result = service.cancel_close_order("55", "etoro-paper-trade")
    assert result["status"] == "ok"
    assert module.calls == ["cancel_close"]


def test_service_cancel_close_order_live_fails_closed(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    _patch_gate(monkeypatch, mandate=_mandate())

    result = service.cancel_close_order("55", "etoro-live-trade")

    assert result["status"] == "blocked"
    assert "reinstated risk" in result["reason"]
    assert module.calls == []


def test_service_edit_position_stops_paper_bypasses_gate(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    result = service.edit_position_stops("7", "etoro-paper-trade", stop_loss=90.0)
    assert result["status"] == "ok"
    assert module.calls == ["edit"]


def test_service_edit_position_stops_live_fails_closed(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    _patch_gate(monkeypatch, mandate=_mandate())

    result = service.edit_position_stops(
        "99",
        "etoro-live-trade",
        stop_loss=90.0,
    )

    assert result["status"] == "blocked"
    assert "incremental USD funding" in result["reason"]
    assert module.calls == []


def test_service_etoro_copy_start_paper_returns_unavailable(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    result = service.etoro_copy_start(
        123,
        100.0,
        "etoro-paper-trade",
        reference_id="ref-1",
    )
    assert result["status"] == "error"
    assert result["error_code"] == "copy_unavailable_on_paper"
    assert module.calls == []


def test_service_etoro_copy_increase_non_usd_live_account_fails_closed(
    monkeypatch,
) -> None:
    module = _FakeEtoroModule(account_currency="EUR")
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    _patch_gate(monkeypatch, mandate=_mandate())

    result = service.etoro_copy_start(
        123,
        100.0,
        "etoro-live-trade",
        reference_id="ref-1",
    )

    assert result["status"] == "blocked"
    assert "verified USD accounts" in result["reason"]
    assert module.calls == []


def test_service_cancel_order_paper_bypasses_gate(monkeypatch) -> None:
    module = _FakeEtoroModule()
    monkeypatch.setattr(service, "_sdk_module", lambda connector: module)
    result = service.cancel_order("42", "etoro-paper-trade", symbol="BTC")
    assert result["status"] == "ok"
    assert module.calls == ["cancel"]
