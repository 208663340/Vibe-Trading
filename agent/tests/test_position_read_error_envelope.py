"""Tests for the error envelopes of the position and balance reads.

A broker API failure must surface as ``status: error`` so the mandate gate
fails closed; flattening a rejected call into an empty list would let the gate
evaluate mandates against an empty book (#1207 Phase 0). All SDK boundaries
are mocked; no network, no credentials.
"""

from __future__ import annotations

import pytest

from src.markets.trading.connectors.futu import sdk as futu_sdk
from src.markets.trading.connectors.okx import sdk as okx_sdk

pytestmark = pytest.mark.unit


class _FakeFutu:
    RET_OK = 0

    class TrdEnv:
        SIMULATE = "SIMULATE"


class _FakeTradeCtx:
    def __init__(self, *, position_result, accinfo_result):
        self._position_result = position_result
        self._accinfo_result = accinfo_result

    def get_acc_list(self):
        return (0, [{"trd_env": "SIMULATE", "acc_id": 123}])

    def position_list_query(self, **_):
        return self._position_result

    def accinfo_query(self, **_):
        return self._accinfo_result

    def close(self):
        pass


def _futu_config():
    return futu_sdk.FutuConfig(acc_id=123)


def test_futu_positions_error_code_returns_error_envelope(monkeypatch) -> None:
    ctx = _FakeTradeCtx(position_result=(1, "permission denied"), accinfo_result=(0, []))
    monkeypatch.setattr(futu_sdk, "_trade_ctx", lambda cfg: ctx)
    monkeypatch.setattr(futu_sdk, "_require_futu", lambda: _FakeFutu)

    result = futu_sdk.get_positions(_futu_config())
    assert result["status"] == "error"
    assert "permission denied" in result["error"]


def test_futu_positions_success_stays_ok(monkeypatch) -> None:
    ctx = _FakeTradeCtx(position_result=(0, []), accinfo_result=(0, []))
    monkeypatch.setattr(futu_sdk, "_trade_ctx", lambda cfg: ctx)
    monkeypatch.setattr(futu_sdk, "_require_futu", lambda: _FakeFutu)

    result = futu_sdk.get_positions(_futu_config())
    assert result["status"] == "ok"
    assert result["positions"] == []


def test_futu_snapshot_error_code_returns_error_envelope(monkeypatch) -> None:
    ctx = _FakeTradeCtx(position_result=(0, []), accinfo_result=(1003, "system busy"))
    monkeypatch.setattr(futu_sdk, "_trade_ctx", lambda cfg: ctx)
    monkeypatch.setattr(futu_sdk, "_require_futu", lambda: _FakeFutu)

    result = futu_sdk.get_account_snapshot(_futu_config())
    assert result["status"] == "error"
    assert "system busy" in result["error"]


class _FakeOKXAccount:
    def __init__(self, resp):
        self._resp = resp

    def get_positions(self):
        return self._resp

    def get_account_balance(self):
        return self._resp


def test_okx_positions_business_error_returns_error_envelope(monkeypatch) -> None:
    resp = {"code": "50011", "msg": "Request too frequent", "data": []}
    monkeypatch.setattr(okx_sdk, "_account_client", lambda cfg: _FakeOKXAccount(resp))

    result = okx_sdk.get_positions(okx_sdk.OKXConfig())
    assert result["status"] == "error"
    assert "50011" in result["error"]


def test_okx_positions_success_stays_ok(monkeypatch) -> None:
    resp = {"code": "0", "data": []}
    monkeypatch.setattr(okx_sdk, "_account_client", lambda cfg: _FakeOKXAccount(resp))

    result = okx_sdk.get_positions(okx_sdk.OKXConfig())
    assert result["status"] == "ok"
    assert result["positions"] == []


def test_okx_snapshot_business_error_returns_error_envelope(monkeypatch) -> None:
    resp = {"code": "50113", "msg": "Invalid sign", "data": []}
    monkeypatch.setattr(okx_sdk, "_account_client", lambda cfg: _FakeOKXAccount(resp))

    result = okx_sdk.get_account_snapshot(okx_sdk.OKXConfig())
    assert result["status"] == "error"
    assert "50113" in result["error"]
